from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..utils.viewsets import ModelViewSet
from ..utils.response import ErrorResponse
from .models import Card
from .serializers import CardSerializer, CardCommentSerializer
from .permissions import CardPermission


class CardViewSet(ModelViewSet):
    model = Card
    serializer_class = CardSerializer
    permission_classes = (CardPermission, )
    filter_backends = (filters.DjangoFilterBackend,)
    filter_fields = ('board', )

    def get_queryset(self):
        board = self.request.QUERY_PARAMS.get('board')
        user = self.request.user

        if user.is_authenticated():
            user = self.request.user
            return user.cards.prefetch_related('cards')

        cards = Card.objects.all(board__is_shared=True)

        if board:
            return cards.filter(board_id=board)

        return cards

    @action(methods=['GET', 'POST'], serializer_class=CardCommentSerializer)
    def comments(self, request, pk=None):
        card = self.get_object()

        if request.method == 'POST':
            context = self.get_serializer_context()

            context.update({
                'content_object': card
            })

            serializer = CardCommentSerializer(
                data=request.DATA, context=context)

            if serializer.is_valid():
                serializer.save()

                headers = self.get_success_headers(serializer.data)

                return Response(
                    serializer.data,
                    status=status.HTTP_201_CREATED,
                    headers=headers)
            else:
                return ErrorResponse(serializer.errors)
        else:
            comments = card.comments.all()
            serializer = CardCommentSerializer(comments, many=True)

        return Response(serializer.data)
