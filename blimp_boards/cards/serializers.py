from rest_framework import serializers

from ..comments.models import Comment
from .models import Card


class CardSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Card

    def validate_cards(self, attrs, source):
        cards = attrs[source]
        card_type = attrs.get('type')

        if card_type != 'stack':
            attrs[source] = []
            return attrs

        request = self.context['request']

        user_cards_ids = request.user.cards.values_list('id', flat=True)

        for card in cards:
            if card.type == 'stack' or card == self.object \
                    or card.id not in user_cards_ids:
                msg = 'Invalid value.'
                raise serializers.ValidationError(msg)

        return attrs

    def validate_content(self, attrs, source):
        content = attrs.get(source)
        card_type = attrs.get('type')

        if card_type != 'stack' and not content:
            raise serializers.ValidationError(self.error_messages['required'])

        return attrs

    def save_object(self, obj, **kwargs):
        obj.created_by = self.context['request'].user
        return super(CardSerializer, self).save_object(obj, **kwargs)


class CardCommentSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Comment
        fields = ('id', 'content', 'created_by',
                  'date_created', 'date_modified')

    def save_object(self, obj, **kwargs):
        obj.content_object = self.context['content_object']
        obj.created_by = self.context['request'].user
        return super(CardCommentSerializer, self).save_object(obj, **kwargs)