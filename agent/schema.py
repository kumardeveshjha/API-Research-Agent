"""Strict answer format for every app. Enums (not free text) so results can be clustered."""
from typing import Literal, Optional
from pydantic import BaseModel, Field

Auth = Literal["oauth2", "api_key", "basic", "bearer_token", "jwt", "bot_token", "none", "other", "unclear"]
Access = Literal[
    "self_serve_free",    # anyone can sign up and get credentials for free
    "trial",              # free trial gives credentials
    "paid_required",      # needs a paid plan to get API access
    "approval_required",  # app review / admin approval / verification
    "partner_gated",      # partnership or contact-sales needed
    "no_api",             # no public API at all
    "unclear",
]
ApiType = Literal["rest", "graphql", "both", "cli_only", "sdk_only", "none", "unclear"]
Blocker = Literal["none", "paid_plan", "partner_approval", "contact_sales",
                  "no_public_api", "poor_docs", "other"]


class Evidence(BaseModel):
    field: str = Field(description="Which field this supports: auth_methods, access, api_type, existing_mcp, buildability ...")
    url: str
    quote: str = Field(description="EXACT text copied from the page. Never paraphrase.")


class AppResult(BaseModel):
    name: str
    category: str
    one_liner: str
    auth_methods: list[Auth]
    access: Access
    api_type: ApiType
    api_breadth: Literal["small", "medium", "large", "unclear"] = "unclear"
    endpoint_estimate: Optional[str] = None
    existing_mcp: Literal["official", "community", "none", "unclear"] = "unclear"
    mcp_url: Optional[str] = None
    buildability: Literal["ready", "ready_with_caveats", "blocked"] = "ready_with_caveats"
    main_blocker: Blocker = "none"
    webhooks: Literal["yes", "no", "unclear"] = "unclear"
    openapi_spec: Literal["yes", "no", "unclear"] = "unclear"
    rate_limits_documented: Literal["yes", "no", "unclear"] = "unclear"
    sandbox_or_test_mode: Literal["yes", "no", "unclear"] = "unclear"
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: Literal["high", "med", "low"] = "low"
    needs_human: bool = False
    notes: str = ""