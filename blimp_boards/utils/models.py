import reversion

from django.db import models, transaction
from django.conf import settings
from django.utils.encoding import smart_text
from django.utils.log import getLogger

from announce import Announce
from rest_framework.renderers import JSONRenderer
from rest_framework import serializers

from .fields import DateTimeCreatedField, DateTimeModifiedField
from .mixins import ModelDiffMixin

try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse


logger = getLogger(__name__)

urlparse.uses_netloc.append('redis')

models.options.DEFAULT_NAMES += ('announce', )


def json_renderer(data):
    return smart_text(JSONRenderer().render(data))


class BaseModel(ModelDiffMixin, models.Model):
    """
    An abstract base class model that provides:
        - date_created
        - date_modified
    """
    date_created = DateTimeCreatedField()
    date_modified = DateTimeModifiedField()

    class Meta:
        get_latest_by = 'date_modified'
        ordering = ('-date_modified', '-date_created',)
        abstract = True

    @property
    def serializer(self):
        class ModelSerializer(serializers.ModelSerializer):
            class Meta:
                model = self.__class__

        return ModelSerializer(self)

    def save(self, *args, **kwargs):
        """
        Group any changes to models into a revision.
        """
        revisions = getattr(self._meta, 'revisions', True)

        try:
            if revisions:
                with transaction.atomic(), reversion.create_revision():
                    return super(BaseModel, self).save(*args, **kwargs)
        except AttributeError:
            pass

        return super(BaseModel, self).save(*args, **kwargs)

    def to_dict(self):
        """
        Returns a dictionary representation of the model using
        REST framework's model serializers. Uses a specified serializer
        on the model or defaults to a generic ModelSerializer.
        """
        return self.serializer.data

    def set_revisions(self, boolean):
        """
        Allow overriding to turn on/off Meta.revisions
        """
        self._meta.revisions = bool(boolean)

    def set_announce(self, boolean):
        """
        Allow overriding to turn on/off Meta.announce
        """
        self._meta.announce = bool(boolean)

    def announce(self, method):
        """
        Announces to SocketIO Redis store that a model has changed.
        Includes the model name as a data_type, method, and a serialized
        representation of the model instance.
        """
        try:
            room = self.announce_room
        except AttributeError as e:
            logger.exception(e)
            return None

        data = {
            'data_type': self.__class__.__name__.lower(),
            'method': method,
            'data': self.to_dict()
        }

        redis_configuration = {}

        if not settings.ANNOUNCE_TEST_MODE:
            redis_url = urlparse.urlparse(settings.BOARDS_SOCKETS_REDIS_URL)

            redis_configuration.update({
                'host': redis_url.hostname,
                'password': redis_url.password,
                'port': int(redis_url.port) if redis_url.port else 6379,
                'db': int(redis_url.path[1:]) if redis_url.path[1:] else 0,
            })

        announce = Announce(
            json_dumps=json_renderer,
            _test_mode=settings.ANNOUNCE_TEST_MODE,
            **redis_configuration)

        try:
            announce.emit('message', data, room=room)
        except Exception as e:
            logger.exception(e)

    def post_save(self, created, **kwargs):
        """
        If model's Meta class has `announce = True`, announces
        when a model instance is created or updated.
        """
        try:
            if self._meta.announce:
                method = 'create' if created else 'update'
                self.announce(method)
        except AttributeError:
            pass

    def post_delete(self, **kwargs):
        """
        If model's Meta class has `announce = True`, announces
        when a model instance deleted.
        """
        try:
            if self._meta.announce:
                self.announce('delete')
        except AttributeError:
            pass
