"""Agent suite: phases (Discover / Propose / Execute / Analyze) over synthetic_data/.

Each phase is prepare() -> chat() -> parse() -> apply(); only chat() touches a
model, so everything else is unit-testable without tokens. See docs/plan.md."""
