"""
Minimal syntactic reader for Datalog ``.dl`` files.

This module exists so Level-3 participants who consume ``graph/facts.dl``
and ``graph/rules.dl`` from the BioKG-Align public release have a stock
loader available without writing their own parser.

The loader is syntactic only — it parses files into typed Python data
structures (:class:`Atom`, :class:`Fact`, :class:`Rule`) but it does not
evaluate rules. Participants who want rule evaluation should pipe the
output into a Datalog engine (Soufflé, Clingo's lp2normal, or pyDatalog)
of their choice; the parser is engine-agnostic.

Grammar
-------
The dialect is the Soufflé-style fragment emitted by the organiser
pipeline. Two lexical conventions are recognised:

* Lines beginning with ``%`` are comments.
* Each non-comment, non-blank logical line must terminate with ``.``.

A line is either a *fact* or a *rule*:

* **Fact:** ``predicate(arg1, arg2, ...).`` — zero-arity facts like
  ``loaded().`` are valid.
* **Rule:** ``head :- body_atom_1, body_atom_2, ....`` — the body is a
  comma-separated list of atoms; commas inside a single atom's
  argument list are not body separators.

The parser is intentionally strict: a missing terminating period or a
malformed atom raises :class:`ValueError` with a message that points at
the offending line. Strict parsing here matters because the released
``.dl`` files are organiser-generated and any deviation is more likely
a corrupted file than a legitimate variation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Atom:
    """A single Datalog atom: a predicate name plus a tuple of arguments."""

    predicate: str
    args: tuple[str, ...]

    def __str__(self) -> str:
        if not self.args:
            return f"{self.predicate}()"
        return f"{self.predicate}({', '.join(self.args)})"


@dataclass(frozen=True)
class Fact:
    """A ground fact: an atom that holds unconditionally."""

    atom: Atom

    def __str__(self) -> str:
        return f"{self.atom}."


@dataclass(frozen=True)
class Rule:
    """A Horn rule: a head atom derivable from a non-empty body of atoms."""

    head: Atom
    body: tuple[Atom, ...]

    def __str__(self) -> str:
        body_str = ", ".join(str(atom) for atom in self.body)
        return f"{self.head} :- {body_str}."


def _strip_comments_and_blanks(text: str) -> list[tuple[int, str]]:
    """
    Return a list of ``(1-indexed line number, stripped content)`` for
    every line that is neither blank nor a comment. Multi-line
    statements are not supported by the organiser-side writer, so each
    logical statement occupies exactly one line.
    """
    lines: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("%"):
            continue
        lines.append((lineno, stripped))
    return lines


def _parse_atom(s: str) -> Atom:
    """
    Parse a single atom of the form ``pred(arg1, arg2, ...)`` or
    ``pred()``. Whitespace inside the argument list is tolerated.

    Raises
    ------
    ValueError
        If ``s`` does not match the expected atom shape.
    """
    s = s.strip()
    open_paren = s.find("(")
    if open_paren < 0 or not s.endswith(")"):
        raise ValueError(
            f"Datalog atom is missing parentheses: {s!r}. Expected the "
            f"form 'predicate(arg1, arg2, ...)' or 'predicate()'."
        )
    predicate = s[:open_paren].strip()
    if not predicate:
        raise ValueError(
            f"Datalog atom has an empty predicate name: {s!r}."
        )
    inside = s[open_paren + 1 : -1].strip()
    if not inside:
        return Atom(predicate, ())
    args = tuple(arg.strip() for arg in inside.split(","))
    if any(not arg for arg in args):
        raise ValueError(
            f"Datalog atom has an empty argument: {s!r}. Use a "
            f"non-empty token for each positional argument."
        )
    return Atom(predicate, args)


def _split_body_atoms(body: str) -> list[str]:
    """
    Split a rule body into atom-shaped substrings, respecting paren
    nesting so that commas inside an atom's argument list don't act as
    body separators.
    """
    atoms: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(
                    f"Unbalanced parentheses in rule body: {body!r}."
                )
        elif ch == "," and depth == 0:
            atom_str = body[start:i].strip()
            if atom_str:
                atoms.append(atom_str)
            start = i + 1
    tail = body[start:].strip()
    if tail:
        atoms.append(tail)
    if depth != 0:
        raise ValueError(
            f"Unbalanced parentheses in rule body: {body!r}."
        )
    return atoms


def _parse_statement(lineno: int, content: str) -> Fact | Rule:
    """Parse a single non-comment line into either a Fact or a Rule."""
    if not content.endswith("."):
        raise ValueError(
            f"Line {lineno}: Datalog statements must end with '.': "
            f"{content!r}."
        )
    body_text = content[:-1].strip()  # drop the trailing period
    if ":-" in body_text:
        head_str, body_str = body_text.split(":-", 1)
        head = _parse_atom(head_str.strip())
        body_atoms = tuple(_parse_atom(a) for a in _split_body_atoms(body_str.strip()))
        if not body_atoms:
            raise ValueError(
                f"Line {lineno}: rule has an empty body: {content!r}."
            )
        return Rule(head=head, body=body_atoms)
    return Fact(atom=_parse_atom(body_text))


def load_facts(path: str | Path) -> list[Fact]:
    """
    Read a ``.dl`` file containing ground facts.

    Lines containing ``:-`` are rejected: facts are unconditional, by
    definition. Use :func:`load_rules` to read a rules file (or a mixed
    file, splitting yourself).

    Returns
    -------
    list[Fact]
        Facts in file order. Comments and blank lines are skipped.

    Raises
    ------
    ValueError
        On any malformed statement; the message identifies the line.
    """
    text = Path(path).read_text(encoding="utf-8")
    facts: list[Fact] = []
    for lineno, content in _strip_comments_and_blanks(text):
        parsed = _parse_statement(lineno, content)
        if isinstance(parsed, Rule):
            raise ValueError(
                f"Line {lineno}: expected a fact, found a rule "
                f"({content!r}). Use load_rules() for rule files."
            )
        facts.append(parsed)
    return facts


def load_rules(path: str | Path) -> list[Rule]:
    """
    Read a ``.dl`` file containing Horn rules.

    Lines without ``:-`` are rejected: rules must have a non-empty body.
    Use :func:`load_facts` to read a facts-only file.

    Returns
    -------
    list[Rule]
        Rules in file order. Comments and blank lines are skipped.

    Raises
    ------
    ValueError
        On any malformed statement; the message identifies the line.
    """
    text = Path(path).read_text(encoding="utf-8")
    rules: list[Rule] = []
    for lineno, content in _strip_comments_and_blanks(text):
        parsed = _parse_statement(lineno, content)
        if isinstance(parsed, Fact):
            raise ValueError(
                f"Line {lineno}: expected a rule, found a fact "
                f"({content!r}). Use load_facts() for facts files."
            )
        rules.append(parsed)
    return rules
