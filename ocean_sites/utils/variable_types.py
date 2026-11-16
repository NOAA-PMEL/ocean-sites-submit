from typing import Optional


def infer_special_variable_type(name: str) -> Optional[str]:
    """Return the OceanSITES special type indicated by a variable-name suffix."""
    normalized_name = (name or "").upper()
    for suffix, var_type in (
        ("_QC", "qc"),
        ("_DM", "dm"),
        ("_UNCERTAINTY", "uncertainty"),
    ):
        if normalized_name.endswith(suffix):
            return var_type
    if "QC" in normalized_name:
        return "qc"
    return None