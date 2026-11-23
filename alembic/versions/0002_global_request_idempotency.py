"""Global request idempotency (unique client_request_id and role).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23 20:30:00.000000

Schema changes
--------------
Replaces uq_messages_conversation_request_role (conversation_id, client_request_id, role)
with uq_messages_client_request_role (client_request_id, role) to enforce global request
idempotency across all conversations.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_constraint("uq_messages_conversation_request_role", type_="unique")
        batch_op.create_unique_constraint(
            "uq_messages_client_request_role",
            ["client_request_id", "role"],
        )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_constraint("uq_messages_client_request_role", type_="unique")
        batch_op.create_unique_constraint(
            "uq_messages_conversation_request_role",
            ["conversation_id", "client_request_id", "role"],
        )
