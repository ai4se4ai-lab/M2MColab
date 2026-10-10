def parsesRisk(raw: str) -> bool:
    return (raw or "").strip().lower() in {"low", "medium", "high"}
