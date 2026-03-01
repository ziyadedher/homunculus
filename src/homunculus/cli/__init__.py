"""CLI package — chat REPL and admin commands."""

from homunculus.cli.admin import (
    run_audit_log,
    run_contacts_add,
    run_contacts_edit,
    run_contacts_list,
    run_contacts_rm,
    run_conversation_detail,
    run_conversations_list,
    run_dashboard,
)
from homunculus.cli.chat import OWNER_REFRESH_SECONDS, run_chat

__all__ = [
    "OWNER_REFRESH_SECONDS",
    "run_audit_log",
    "run_chat",
    "run_contacts_add",
    "run_contacts_edit",
    "run_contacts_list",
    "run_contacts_rm",
    "run_conversation_detail",
    "run_conversations_list",
    "run_dashboard",
]
