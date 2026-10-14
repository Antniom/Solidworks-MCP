"""
SolidWorks MCP Core Services
----------------------------
Core architecture services: STA COM worker, typed response envelopes,
filesystem security policy, capabilities reporting, declarative selection,
and model verification engine.
"""

from .envelope import ResponseEnvelope, create_envelope, error_envelope, success_envelope
from .com_worker import STAWorker, get_sta_worker, reset_sta_worker, SolidWorksModalBlockedException, AbandonedCallRunningException, find_blocking_modal_dialog
from .fs_policy import FileSystemPolicy, get_fs_policy
from .capabilities import CapabilityInspector
from .selection import DeclarativeSelectionResolver, SelectionTarget, SelectionResult
from .verification import ModelVerifier, VerificationReport, DimensionCheck
from .knowledge import KnowledgeManager, KnowledgeRecord, PromotionState, get_knowledge_manager
from .checkpoint import CheckpointManager, CheckpointMetadata, get_checkpoint_manager
from .com_compat import get_attr_or_call, byref_int, byref_bool, byref_double, null_dispatch
from .post_conditions import capture_pre_mutation_state, verify_mutation_post_condition, PreMutationState, get_model_rebuild_errors

__all__ = [
    "ResponseEnvelope",
    "create_envelope",
    "error_envelope",
    "success_envelope",
    "STAWorker",
    "get_sta_worker",
    "reset_sta_worker",

    "FileSystemPolicy",
    "get_fs_policy",
    "CapabilityInspector",
    "DeclarativeSelectionResolver",
    "SelectionTarget",
    "SelectionResult",
    "ModelVerifier",
    "VerificationReport",
    "DimensionCheck",
    "KnowledgeManager",
    "KnowledgeRecord",
    "PromotionState",
    "get_knowledge_manager",
    "CheckpointManager",
    "CheckpointMetadata",
    "get_checkpoint_manager",
    "get_attr_or_call",
    "byref_int",
    "byref_bool",
    "byref_double",
    "null_dispatch",
    "capture_pre_mutation_state",
    "verify_mutation_post_condition",
    "PreMutationState",
    "get_model_rebuild_errors",
]
