import jwt

from django.conf import settings
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models.loading import get_model
from django.utils.encoding import python_2_unicode_compatible

from ..notifications.signals import notify
from ..users.models import User
from ..users.utils import get_gravatar_url
from ..utils.decorators import autoconnect
from ..utils.models import BaseModel
from .managers import SignupRequestManager, InvitedUserManager


@python_2_unicode_compatible
class SignupRequest(BaseModel):
    email = models.EmailField(unique=True)
    objects = SignupRequestManager()

    def __str__(self):
        return self.email

    @property
    def token(self):
        """
        Returns a JSON Web Token
        """
        payload = {
            'type': 'SignupRequest',
            'email': self.email,
        }

        jwt_token = jwt.encode(payload, settings.SECRET_KEY)

        return jwt_token.decode('utf-8')

    def send_email(self):
        actor = None
        recipients = [self.email]
        label = 'signup_request_created'

        extra_context = {
            'action_object': self,
        }

        notify.send(
            actor,
            recipients=recipients,
            label=label,
            extra_context=extra_context,
            override_backends=('email', )
        )


@autoconnect
@python_2_unicode_compatible
class InvitedUser(BaseModel):
    first_name = models.CharField(max_length=30, blank=True)
    last_name = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    user = models.ForeignKey('users.User', null=True, blank=True)
    account = models.ForeignKey('accounts.Account')
    created_by = models.ForeignKey(
        'users.User', related_name='%(class)s_created_by')

    board_collaborator = models.ForeignKey('boards.BoardCollaborator',
                                           blank=True, null=True)

    objects = InvitedUserManager()

    class Meta:
        unique_together = (
            ('account', 'email')
        )

    def __str__(self):
        return self.email

    @property
    def token(self):
        """
        Returns a JSON Web Token
        """
        payload = {
            'type': 'InvitedUser',
            'pk': self.pk,
            'email': self.email
        }

        jwt_token = jwt.encode(payload, settings.SECRET_KEY)

        return jwt_token.decode('utf-8')

    @property
    def gravatar_url(self):
        return get_gravatar_url(self.email)

    @property
    def username(self):
        return self.user.username if self.user_id else None

    @property
    def invite_url(self):
        if self.user_id:
            url = reverse('auth-signin')
        else:
            url = reverse('auth-signup')

        return '{}?invite={}'.format(url, self.token)

    def save(self, *args, **kwargs):
        """
        When saving a InvitedUser, try to set first_name,
        last_name, and email if a user is given. If no user is given,
        try to find an existing user with matching email.
        """
        if not self.user:
            try:
                self.user = User.objects.get(email=self.email)
            except User.DoesNotExist:
                pass

        if self.user:
            self.first_name = self.user.first_name
            self.last_name = self.user.last_name
            self.email = self.user.email

        return super(InvitedUser, self).save(*args, **kwargs)

    def post_save(self, created, *args, **kwargs):
        """
        Create a SignupRequest in case user wants to reject but signup.
        """
        if created and not self.user:
            SignupRequest.objects.get_or_create(email=self.email)

        super(InvitedUser, self).post_save(created, *args, **kwargs)

    def get_email(self):
        return self.user.email if self.user else self.email

    def get_full_name(self):
        """
        Returns the first_name plus the last_name, with a space in between.
        """
        full_name = u'{} {}'.format(self.first_name, self.last_name)
        return full_name.strip()

    def accept(self, user):
        """
        - Create AccountCollaborator
        - Set user to BoardCollaborators
        - Delete any SignupRequest
        - Delete invitation
        """
        AccountCollaborator = get_model('accounts', 'AccountCollaborator')

        collaborator = AccountCollaborator.objects.create(
            user=user, account=self.account)

        if self.board_collaborator:
            self.board_collaborator.user = user
            self.board_collaborator.invited_user = None
            self.board_collaborator.save()

        SignupRequest.objects.filter(email=self.email).delete()

        self.delete()

        return collaborator

    def reject(self):
        """
        TODO: Notify created_by
        """
        self.delete()

    def send_invite(self):
        actor = self.created_by
        recipients = [self.email]
        label = 'user_invited'

        extra_context = {
            'action_object': self,
        }

        notify.send(
            actor,
            recipients=recipients,
            label=label,
            extra_context=extra_context,
            override_backends=('email', )
        )
