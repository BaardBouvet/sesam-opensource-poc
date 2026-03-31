"""Parse mapping.yaml into a typed structure the dashboard uses everywhere."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class FieldDef:
    name: str
    strategy: str  # identity | coalesce | last_modified | collect
    references: Optional[str] = None  # target name for FK fields


@dataclass
class WrittenStateDef:
    table: str
    cluster_id_col: str
    written_col: str
    written_at_col: str


@dataclass
class MappingDef:
    name: str
    source: str  # source name key
    source_table: str  # inout_src_* table
    target: str  # target name key
    written_state: Optional[WrittenStateDef]
    parent: Optional[str] = None  # for derived mappings like tripletex_contact_assoc


@dataclass
class TargetDef:
    name: str
    fields: list[FieldDef]
    mappings: list[MappingDef]


@dataclass
class Mapping:
    sources: dict[str, str]  # source_name -> table_name
    targets: dict[str, TargetDef]  # target_name -> TargetDef
    mappings: list[MappingDef]


def load_mapping(path: str) -> Mapping:
    data = yaml.safe_load(Path(path).read_text())

    sources: dict[str, str] = {
        name: cfg["table"] for name, cfg in data.get("sources", {}).items()
    }

    raw_mappings: list[MappingDef] = []
    for m in data.get("mappings", []):
        ws_raw = m.get("written_state")
        ws = (
            WrittenStateDef(
                table=ws_raw["table"],
                cluster_id_col=ws_raw.get("cluster_id", "cluster_id"),
                written_col=ws_raw.get("written", "data"),
                written_at_col=ws_raw.get("written_at", "_written_at"),
            )
            if ws_raw
            else None
        )
        src_name = m.get("source") or m.get("parent", "")
        raw_mappings.append(
            MappingDef(
                name=m["name"],
                source=src_name,
                source_table=sources.get(src_name, ""),
                target=m["target"],
                written_state=ws,
                parent=m.get("parent"),
            )
        )

    targets: dict[str, TargetDef] = {}
    for target_name, t_cfg in data.get("targets", {}).items():
        fields = []
        for fname, fdef in t_cfg.get("fields", {}).items():
            if isinstance(fdef, str):
                strategy = fdef
                refs = None
            else:
                strategy = fdef.get("strategy", "coalesce")
                refs = fdef.get("references")
            fields.append(FieldDef(name=fname, strategy=strategy, references=refs))
        t_mappings = [m for m in raw_mappings if m.target == target_name]
        targets[target_name] = TargetDef(
            name=target_name, fields=fields, mappings=t_mappings
        )

    return Mapping(sources=sources, targets=targets, mappings=raw_mappings)
