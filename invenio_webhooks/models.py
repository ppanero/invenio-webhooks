# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2014, 2015 CERN.
#
# Invenio is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# Invenio is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Invenio; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, CERN does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

"""Models for webhook receivers."""

from __future__ import absolute_import

import re

from flask import current_app, request, url_for

from . import signatures
from .proxies import current_webhooks


#
# Errors
#
class WebhookError(Exception):
    """General webhook error."""

    pass


class ReceiverDoesNotExist(WebhookError):
    """Raised when receiver does not exist."""

    pass


class InvalidPayload(WebhookError):
    """Raised when the payload is invalid."""

    pass


class InvalidSignature(WebhookError):
    """Raised when the signature does not match."""

    pass


#
# Models
#
class Receiver(object):
    """Base class for a webhook receiver.

    A receiver is responsible for receiving and extracting a payload from a
    request, and passing it on to a method which can handle the event
    notification.
    """

    def __init__(self, fn, signature=''):
        """Initialize a receiver with a callable and a signature."""
        self._callable = fn
        self.signature = signature

    @classmethod
    def get(cls, receiver_id):
        """Return registered receiver."""
        try:
            return current_webhooks.receivers[receiver_id]
        except KeyError:
            raise ReceiverDoesNotExist(receiver_id)

    @classmethod
    def all(cls):
        """Return all registered receivers."""
        return current_webhooks.receivers

    @classmethod
    def register(cls, receiver_id, receiver):
        """Register a webhook receiver.

        :param receiver_id: Receiver ID used in the URL.
        :param receiver: ``Receiver`` instance.
        """
        current_webhooks.register(receiver_id, receiver)

    @classmethod
    def unregister(cls, receiver_id):
        """Unregister an already registered webhook.

        :param receiver_id: Receiver ID used when registering.
        """
        current_webhooks.unregister(receiver_id)

    @classmethod
    def get_hook_url(cls, receiver_id, access_token):
        """Get URL for webhook.

        In debug and testing mode the hook URL can be overwritten using
        ``WEBHOOKS_DEBUG_RECEIVER_URLS`` configuration variable to allow
        testing webhooks via services such as e.g. Ultrahook.

        .. code-block:: python

            WEBHOOKS_DEBUG_RECEIVER_URLS = dict(
                github='http://github.userid.ultrahook.com',
            )
        """
        cls.get(receiver_id)
        # Allow overwriting hook URL in debug mode.
        if (current_app.debug or current_app.testing) and \
           current_app.config.get('WEBHOOKS_DEBUG_RECEIVER_URLS', None):
            url_pattern = current_app.config[
                'WEBHOOKS_DEBUG_RECEIVER_URLS'].get(receiver_id, None)
            if url_pattern:
                return url_pattern % dict(token=access_token)
        return url_for(
            'invenio_webhooks.event_list',
            receiver_id=receiver_id,
            access_token=access_token,
            _external=True
        )

    #
    # Instance methods (override if needed)
    #
    def consume_event(self, user_id):
        """Consume a webhook event by calling the associated callable."""
        event = self._create_event(user_id)
        self._callable(event)

    def _create_event(self, user_id):
        """Create a new webhook event."""
        return Event(
            user_id,
            payload=self.extract_payload()
        )

    def check_signature(self):
        """Check signature of signed request."""
        if not self.signature:
            return True
        signature_value = request.headers.get(self.signature, None)
        if signature_value:
            validator = 'check_' + re.sub(r'[-]', '_', self.signature).lower()
            check_signature = getattr(signatures, validator)
            if check_signature(signature_value, request.data):
                return True
        return False

    def extract_payload(self):
        """Extract payload from request."""
        if not self.check_signature():
            raise InvalidSignature('Invalid Signature')
        if request.content_type == 'application/json':
            return request.get_json()
        elif request.content_type == 'application/x-www-form-urlencoded':
            return dict(request.form)
        raise InvalidPayload(request.content_type)


class CeleryReceiver(Receiver):
    """Asynchronous receiver.

    Receiver which will fire a celery task to handle payload instead of running
    it synchronously during the request.
    """

    def __init__(self, task_callable, signature='', **options):
        """Initialize a celery receiver."""
        super(CeleryReceiver, self).__init__(task_callable, signature)
        self._task = task_callable
        self._options = options
        from celery import Task
        assert isinstance(self._task, Task)

    def consume_event(self, user_id):
        """Consume a webhook event by firing celery task."""
        event = self._create_event(user_id)
        self._task.apply_async(args=[event.__getstate__()], **self._options)


class Event(object):
    """Incoming webhook event data.

    Represents webhook event data which consists of a payload and a user id.
    """

    def __init__(self, user_id=None, payload=None):
        """Initialize an event with user identifier and payload."""
        self.user_id = user_id
        self.payload = payload

    def __getstate__(self):
        """Pickle current state."""
        return dict(
            user_id=self.user_id,
            payload=self.payload,
        )

    def __setstate__(self, state):
        """Unpickle state."""
        self.user_id = state['user_id']
        self.payload = state['payload']
