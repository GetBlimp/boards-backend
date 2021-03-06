import jwt
import positions
import datetime

from django.conf import settings
from django.contrib.contenttypes import generic
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import F
from django.db.models.loading import get_model
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils.encoding import python_2_unicode_compatible
from django.utils.functional import cached_property
from django.utils.timezone import now

from jsonfield import JSONField
from rest_framework.utils.encoders import JSONEncoder

from ..files.previews import queue_previews
from ..files.utils import sign_s3_url, generate_file_key
from ..notifications.signals import notify
from ..utils.decorators import autoconnect
from ..utils.fields import ReservedKeywordsAutoSlugField
from ..utils.models import BaseModel
from .constants import CARD_RESERVED_KEYWORDS
from .managers import CardManager


@autoconnect
@python_2_unicode_compatible
class Card(BaseModel):
    PREVIEWABLE_TYPES = ('link', 'file', )
    TYPE_CHOICES = (
        ('link', 'Link'),
        ('note', 'Note'),
        ('file', 'File'),
        ('stack', 'Stack'),
    )

    name = models.CharField(max_length=255)
    type = models.CharField(max_length=5, choices=TYPE_CHOICES)
    slug = ReservedKeywordsAutoSlugField(
        editable=True, blank=True, populate_from='name',
        unique_with='board', reserved_keywords=CARD_RESERVED_KEYWORDS)

    board = models.ForeignKey('boards.Board')
    created_by = models.ForeignKey('users.User')
    modified_by = models.ForeignKey('users.User',
                                    related_name='%(class)s_modified_by')

    position = positions.PositionField(collection='board')

    stack = models.ForeignKey(
        'cards.Card', blank=True, null=True, related_name='+')
    cards = models.ManyToManyField(
        'cards.Card', blank=True, null=True, related_name='+')

    featured = models.BooleanField(default=False)
    origin_url = models.URLField(blank=True, null=True)
    content = models.TextField(blank=True, null=True)

    is_shared = models.BooleanField(default=False)

    thumbnail_xs_path = models.TextField(blank=True, null=True)
    thumbnail_sm_path = models.TextField(blank=True, null=True)
    thumbnail_md_path = models.TextField(blank=True, null=True)
    thumbnail_lg_path = models.TextField(blank=True, null=True)

    file_size = models.IntegerField(blank=True, null=True)
    mime_type = models.CharField(max_length=255, blank=True, null=True)

    data = JSONField(blank=True, null=True, dump_kwargs={
                     'cls': JSONEncoder, 'separators': (',', ':')})

    comments_count = models.PositiveIntegerField(default=0)
    comments = generic.GenericRelation('comments.Comment')

    objects = CardManager()

    class Meta:
        announce = True
        ordering = ['position']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('card_detail', kwargs={
            'account_slug': self.board.account.slug,
            'board_slug': self.board.slug,
            'card_slug': self.slug})

    @cached_property
    def html_url(self):
        return '{}{}'.format(settings.APPLICATION_URL, self.get_absolute_url())

    @cached_property
    def download_html_url(self):
        return '{}?download'.format(self.html_url)

    @cached_property
    def original_html_url(self):
        return '{}?original'.format(self.html_url)

    @cached_property
    def announce_room(self):
        return 'a{}'.format(self.board.account_id)

    @cached_property
    def serializer_class(self):
        from .serializers import CardSerializer
        return CardSerializer

    @cached_property
    def serializer(self):
        return self.serializer_class(self)

    @property
    def signed_thumbnail_xs_path(self):
        if self.thumbnail_xs_path:
            return sign_s3_url(self.thumbnail_xs_path)

    @property
    def signed_thumbnail_sm_path(self):
        if self.thumbnail_sm_path:
            return sign_s3_url(self.thumbnail_sm_path)

    @property
    def signed_thumbnail_md_path(self):
        if self.thumbnail_md_path:
            return sign_s3_url(self.thumbnail_md_path)

    @property
    def signed_thumbnail_lg_path(self):
        if self.thumbnail_lg_path:
            return sign_s3_url(self.thumbnail_lg_path)

    @property
    def download_url(self):
        expire_in = settings.AWS_SIGNATURE_EXPIRES_IN

        payload = {
            'type': 'CardDownload',
            'id': self.id,
            'exp': now() + datetime.timedelta(seconds=expire_in)
        }

        jwt_token = jwt.encode(payload, settings.SECRET_KEY)

        absolute_url = reverse('card_download', kwargs={
            'account_slug': self.board.account.slug,
            'board_slug': self.board.slug,
            'card_slug': self.slug})

        return '{}{}?token={}'.format(
            settings.APPLICATION_URL,
            absolute_url,
            jwt_token.decode('utf-8')
        )

    @property
    def file_download_url(self):
        if self.type == 'file':
            headers = {
                'response-content-disposition': 'attachment'
            }

            return sign_s3_url(self.content, response_headers=headers)

    @property
    def original_thumbnail_url(self):
        if not self.data:
            return None

        thumbnails = self.data.get('thumbnails')

        if not thumbnails:
            return None

        url = None

        for thumbnail in thumbnails:
            size = thumbnail.get('requested_size')
            resized = thumbnail.get('resized')

            if size == 'original' or not resized:
                url = thumbnail.get('url')
                break

        return sign_s3_url(url) if url else None

    @cached_property
    def pattern(self):
        if not self.data:
            return None

        pattern = self.data.get('pattern')

        if pattern:
            return {
                'shape': pattern.get('shape'),
                'color': pattern.get('color'),
            }

    @cached_property
    def metadata(self):
        if not self.data:
            return None

        return {
            'pattern': self.pattern
        }

    def save(self, *args, **kwargs):
        """
        Performs all steps involved in validating before
        model object is saved and sets modified_by
        from created_by when creating.
        """
        if not self.pk and not self.modified_by_id:
            self.modified_by_id = self.created_by_id

        self.clean()

        return super(Card, self).save(*args, **kwargs)

    def post_save(self, created, *args, **kwargs):
        # Notify card was created
        if created:
            self.notify_created()

        super(Card, self).post_save(created, *args, **kwargs)

    def clean(self):
        """
        Validates when card is a stack, that card specific fields arent' set.
        """
        string_fields = [
            'thumbnail_xs_path', 'thumbnail_sm_path', 'thumbnail_md_path',
            'thumbnail_lg_path', 'content', 'origin_url', 'mime_type']

        for field in string_fields:
            if field is None:
                setattr(self, field, '')

        if self.type != 'stack':
            if not self.content:
                raise ValidationError('The `content` field is required.')

            return None

        disallowed_fields = [
            'origin_url', 'content', 'thumbnail_xs_path',
            'thumbnail_sm_path', 'thumbnail_md_path', 'thumbnail_lg_path',
            'file_size', 'mime_type']

        for field in disallowed_fields:
            if getattr(self, field):
                msg = 'The `{}` field should not be set on a card stack.'
                raise ValidationError(msg.format(field))

    def request_previews(self):
        if self.type not in self.PREVIEWABLE_TYPES or not self.content:
            return None

        destination = None

        if self.type == 'file':
            url = sign_s3_url(self.content)
        elif self.type == 'link':
            url = self.content
            destination = generate_file_key()

        sizes = ['original', '42>', '200>', '500>', '800>']

        metadata = {
            'cardId': self.id
        }

        return queue_previews(url, sizes, metadata,
                              uploader_destination=destination)

    def update_notification_data(self):
        """
        Updates thumbnail fields in notifications where this
        card is an action_object.
        """
        Notification = get_model('notifications', 'Notification')

        card_type = ContentType.objects.get_for_model(Card)
        notifications = Notification.objects.filter(
            action_object_content_type=card_type,
            action_object_object_id=self.id)

        update_fields = ('thumbnail_sm_path', 'thumbnail_md_path',
                         'thumbnail_lg_path')

        serializer = self.serializer_class(self, fields=update_fields)

        for notification in notifications:
            notification.data['action_object'].update(serializer.data)
            notification.save()

    def notify_created(self):
        user = self.created_by

        actor = user
        recipients = [user]

        if self.type == 'stack':
            label = 'card_stack_created'
        else:
            label = 'card_created'

        extra_context = {
            'action_object': self,
            'target': self.board
        }

        notify.send(
            actor,
            recipients=recipients,
            label=label,
            extra_context=extra_context
        )

    def notify_featured(self, user):
        actor = user
        recipients = [user]
        label = 'card_featured'

        extra_context = {
            'action_object': self,
            'target': self.board
        }

        notify.send(
            actor,
            recipients=recipients,
            label=label,
            extra_context=extra_context
        )

    def notify_comment_created(self, user, comment):
        recipients = []

        actor = user
        recipients = recipients
        label = 'card_comment_created'

        extra_context = {
            'action_object': comment,
            'description': comment.content,
            'target': self
        }

        notify.send(
            actor,
            recipients=recipients,
            label=label,
            extra_context=extra_context
        )

    def update_comments_count(self, count=1):
        # Temporarily turn off announce
        self.set_announce(False)

        # Update comments count safely
        self.comments_count = F('comments_count') + count
        self.save()

        # Turn on announce
        self.set_announce(True)

        # Reload object and trigger post_save/announce
        card = Card.objects.get(pk=self.pk)
        card.post_save(instance=card, created=False)


@receiver(m2m_changed, sender=Card.cards.through)
def cards_changed(sender, **kwargs):
    """
    Sets the `stack` field to reference a card's stack.
    """
    instance = kwargs['instance']
    action = kwargs['action']
    pk_set = kwargs['pk_set']

    if action == 'post_add':
        Card.objects.filter(pk__in=pk_set).update(stack=instance)
    elif action == 'post_remove':
        Card.objects.filter(pk__in=pk_set, stack=instance).update(stack=None)
    elif action == 'post_clear':
        Card.objects.filter(stack=instance).update(stack=None)
