"""Syntactic reader for the Soufflé programs released by BioKG-Align."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias


@dataclass(frozen=True)
class Atom:
    """A predicate name plus positional arguments."""

    predicate: str
    args: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.predicate}({', '.join(self.args)})"


@dataclass(frozen=True)
class Fact:
    """A ground atom that holds unconditionally."""

    atom: Atom

    def __str__(self) -> str:
        return f"{self.atom}."


@dataclass(frozen=True)
class Comparison:
    """A comparison occurring in a rule body."""

    left: str
    operator: str
    right: str

    def __str__(self) -> str:
        return f"{self.left} {self.operator} {self.right}"


BodyClause: TypeAlias = Atom | Comparison


@dataclass(frozen=True)
class Rule:
    """An atom head derivable from non-empty atom/comparison body clauses."""

    head: Atom
    body: tuple[BodyClause, ...]

    def __str__(self) -> str:
        return f"{self.head} :- {', '.join(str(clause) for clause in self.body)}."


@dataclass(frozen=True)
class Directive:
    """A Soufflé directive such as ``.decl``, ``.include``, or ``.output``."""

    name: str
    value: str

    def __str__(self) -> str:
        return f".{self.name}{(' ' + self.value) if self.value else ''}"


@dataclass(frozen=True)
class Program:
    """A parsed program, including directives and optionally included files."""

    directives: tuple[Directive, ...]
    facts: tuple[Fact, ...]
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Term:
    """One stable RDF term from ``datalog_terms.tsv``."""

    term_id: str
    term_type: str
    lexical: str
    datatype: str = ""
    language: str = ""
    ntriples: str = ""


def _strip_inline_comment(raw: str) -> str:
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string and raw[index : index + 2] == "//":
            return raw[:index]
        if not in_string and char == "%":
            return raw[:index]
    return raw


def _strip_comments_and_blanks(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = _strip_inline_comment(raw).strip()
        if stripped:
            lines.append((lineno, stripped))
    return lines


def _split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    in_string = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"Unbalanced parentheses: {value!r}.")
        elif char == "," and depth == 0:
            part = value[start:index].strip()
            if part:
                parts.append(part)
            start = index + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    if depth != 0 or in_string:
        raise ValueError(f"Unbalanced parentheses or quotes: {value!r}.")
    return parts


def _split_body_atoms(body: str) -> list[str]:
    """Compatibility alias for the previous private parser helper."""
    return _split_top_level(body)


def _parse_atom(value: str) -> Atom:
    value = value.strip()
    open_paren = value.find("(")
    if open_paren < 0 or not value.endswith(")"):
        raise ValueError(f"Datalog atom is missing parentheses: {value!r}.")
    predicate = value[:open_paren].strip()
    if not predicate:
        raise ValueError(f"Datalog atom has an empty predicate name: {value!r}.")
    inside = value[open_paren + 1 : -1].strip()
    if not inside:
        return Atom(predicate, ())
    args = tuple(part.strip() for part in _split_top_level(inside))
    if any(not argument for argument in args):
        raise ValueError(f"Datalog atom has an empty argument: {value!r}.")
    return Atom(predicate, args)


def _parse_body_clause(value: str) -> BodyClause:
    value = value.strip()
    if "(" in value:
        return _parse_atom(value)
    for operator in ("!=", "<=", ">=", "=", "<", ">"):
        match = re.match(rf"^(.+?)\s*{re.escape(operator)}\s*(.+)$", value)
        if match:
            return Comparison(match.group(1).strip(), operator, match.group(2).strip())
    raise ValueError(f"Unsupported Datalog rule-body clause: {value!r}.")


def _parse_statement(lineno: int, content: str) -> Fact | Rule:
    if not content.endswith("."):
        raise ValueError(f"Line {lineno}: Datalog statements must end with '.': {content!r}.")
    body_text = content[:-1].strip()
    if ":-" not in body_text:
        return Fact(_parse_atom(body_text))
    head_text, clauses_text = body_text.split(":-", 1)
    clauses = tuple(_parse_body_clause(value) for value in _split_top_level(clauses_text))
    if not clauses:
        raise ValueError(f"Line {lineno}: rule has an empty body: {content!r}.")
    return Rule(_parse_atom(head_text), clauses)


def _parse_directive(content: str) -> Directive:
    match = re.match(r"^\.([A-Za-z_][A-Za-z0-9_-]*)(?:\s+(.*))?$", content)
    if not match:
        raise ValueError(f"Malformed Soufflé directive: {content!r}.")
    return Directive(match.group(1), (match.group(2) or "").strip())


def load_program(path: str | Path, *, resolve_includes: bool = True) -> Program:
    """
    Parse declarations, includes, facts, rules, comparisons, quoted symbols,
    escapes, and inline comments. This function does not evaluate the program.
    """
    return _load_program(Path(path), resolve_includes, set())


def _load_program(path: Path, resolve_includes: bool, seen: set[Path]) -> Program:
    resolved = path.resolve()
    if resolved in seen:
        return Program((), (), ())
    seen.add(resolved)
    directives: list[Directive] = []
    facts: list[Fact] = []
    rules: list[Rule] = []
    for lineno, content in _strip_comments_and_blanks(path.read_text(encoding="utf-8")):
        if content.startswith("."):
            directive = _parse_directive(content)
            directives.append(directive)
            if directive.name == "include" and resolve_includes:
                match = re.match(r'^"((?:[^"\\]|\\.)+)"$', directive.value)
                if not match:
                    raise ValueError(f"Line {lineno}: malformed .include path: {content!r}.")
                include_name = bytes(match.group(1), "utf-8").decode("unicode_escape")
                included = _load_program(path.parent / include_name, True, seen)
                directives.extend(included.directives)
                facts.extend(included.facts)
                rules.extend(included.rules)
            if directive.name == "input":
                # The v1.1 release layout ships facts as one TSV per relation,
                # referenced from facts.dl via .input directives. Resolving them
                # here keeps load_facts/load_program returning the same Fact
                # objects an inline facts file would have produced.
                facts.extend(_load_input_facts(lineno, directive.value, path.parent))
            continue
        statement = _parse_statement(lineno, content)
        if isinstance(statement, Fact):
            facts.append(statement)
        else:
            rules.append(statement)
    return Program(tuple(directives), tuple(facts), tuple(rules))


def _load_input_facts(lineno: int, value: str, base_dir: Path) -> list[Fact]:
    """Materialize the facts referenced by one ``.input`` directive."""
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$", value)
    if not match:
        raise ValueError(f"Line {lineno}: malformed .input directive: {value!r}.")
    relation = match.group(1)
    params = dict(
        (part.split("=", 1) + [""])[:2]
        for part in (piece.strip() for piece in match.group(2).split(","))
        if part
    )
    filename = params.get("filename", f"{relation}.facts").strip().strip('"')
    delimiter = params.get("delimiter", "\t").strip().strip('"')
    delimiter = bytes(delimiter, "utf-8").decode("unicode_escape") or "\t"
    facts_path = base_dir / filename
    if not facts_path.exists():
        raise FileNotFoundError(
            f"Line {lineno}: .input relation {relation!r} references {facts_path}, "
            "which does not exist next to the program file."
        )
    facts: list[Fact] = []
    for line in facts_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        args = tuple(
            '"' + column.replace("\\", "\\\\").replace('"', '\\"') + '"'
            for column in line.split(delimiter)
        )
        facts.append(Fact(Atom(relation, args)))
    return facts


def load_facts(path: str | Path) -> list[Fact]:
    """Read a facts file. Inline atoms and ``.input``-referenced TSVs both count."""
    program = load_program(path, resolve_includes=False)
    if program.rules:
        raise ValueError(f"{path}: expected facts but found {len(program.rules)} rule(s). Use load_rules().")
    return list(program.facts)


def load_rules(path: str | Path) -> list[Rule]:
    """Read rules from a driver and its includes, ignoring included facts."""
    direct = load_program(path, resolve_includes=False)
    if direct.facts:
        raise ValueError(
            f"{path}: expected rules but found {len(direct.facts)} "
            "direct fact(s). Use load_facts()."
        )
    return list(load_program(path, resolve_includes=True).rules)


def load_terms(path: str | Path) -> dict[str, Term]:
    """Load ``datalog_terms.tsv`` keyed by stable term identifier."""
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle, delimiter="\t")
        return {
            row["term_id"]: Term(
                term_id=row["term_id"],
                term_type=row["term_type"],
                lexical=row["lexical"],
                datatype=row.get("datatype", ""),
                language=row.get("language", ""),
                ntriples=row.get("ntriples", ""),
            )
            for row in rows
        }


def decode_argument(argument: str, terms: dict[str, Term]) -> Term | str:
    """Decode a quoted or unquoted stable term identifier."""
    token = argument.strip()
    if len(token) >= 2 and token[0] == token[-1] == '"':
        token = bytes(token[1:-1], "utf-8").decode("unicode_escape")
    return terms.get(token, argument)
