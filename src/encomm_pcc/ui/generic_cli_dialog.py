"""Generic CLI configuration dialog (Session 007).

A safe, structured editor for the Generic CLI driver: every field maps onto
one validated :class:`~encomm_pcc.drivers.generic_cli_config.GenericCliConfig`
value — there is deliberately **no free-form shell command box**.  The argv
tokens are edited as a whitespace-separated list *of separate tokens*; a
token that must contain spaces is quoted with single quotes and unquoted by
a POSIX-shlex-compatible parser (shlex in POSIX mode never interprets
backslashes, and the resulting tokens are literal argv members handed to
``ProcessSpec`` with ``shell=False`` — no shell ever sees them).

Validation happens through the real ``GenericCliConfig`` constructor before
anything is accepted, so the dialog can only ever produce a configuration the
driver itself will accept.
"""

from __future__ import annotations

import shlex
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..domain import AgentRole
from ..drivers import (
    GenericCliConfig,
    GenericCliConfigError,
    PROMPT_TRANSPORTS,
    RESULT_MODES,
)

__all__ = ["GenericCliConfigDialog"]

_PROMPT_LABELS = {
    "stdin": "stdin (recommended)",
    "temporary_file": "temporary file ({prompt_file} in args)",
}
_RESULT_LABELS = {
    "stdout_text": "stdout text (default)",
    "json": "JSON object (field extraction)",
    "jsonl": "JSON lines (last record wins)",
}


def _display_for_transport(value: str) -> str:
    return _PROMPT_LABELS.get(value, value)


def _display_for_result(value: str) -> str:
    return _RESULT_LABELS.get(value, value)


class GenericCliConfigDialog(QDialog):
    """Modal editor for one role's Generic CLI configuration."""

    #: Upper bound on the token textarea; mirrors GenericCliConfig's argv bound
    #: with room for quoting/spacing (validated exactly by the config itself).
    _MAX_TOKEN_CHARS = 8192

    def __init__(
        self,
        role: AgentRole,
        config_dict: dict | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self.setWindowTitle(f"Generic CLI configuration — {role.value.replace('_', ' ')}")
        self.setModal(True)
        self.setMinimumWidth(520)

        self.executable_edit = QLineEdit()
        self.executable_edit.setPlaceholderText("e.g. opencode  (or an absolute path)")
        self.executable_edit.setToolTip(
            "The CLI executable: a bare command name (resolved on PATH) or an "
            "absolute path. This is NOT a shell command — metacharacters are "
            "not interpreted and must not be entered."
        )

        self.args_edit = QPlainTextEdit()
        self.args_edit.setMaximumHeight(72)
        self.args_edit.setPlaceholderText('e.g. run --format json   (tokens; quote: --prompt "two words")')
        self.args_edit.setToolTip(
            "Literal argv tokens, whitespace separated. Quote a token that "
            "contains spaces: --prompt \"two words\". Metacharacters stay "
            "literal text — no shell interprets them. Placeholders: "
            "{prompt_file}, {workspace}, {model} (whole token only; unknown "
            "placeholders are rejected)."
        )

        self.transport_combo = QComboBox()
        for value in PROMPT_TRANSPORTS:
            self.transport_combo.addItem(_display_for_transport(value), value)

        self.result_combo = QComboBox()
        for value in RESULT_MODES:
            self.result_combo.addItem(_display_for_result(value), value)

        self.result_field_edit = QLineEdit()
        self.result_field_edit.setPlaceholderText("e.g. answer  (dotted path allowed)")
        self.result_field_edit.setEnabled(False)

        self.model_args_edit = QLineEdit()
        self.model_args_edit.setPlaceholderText('e.g. --model {model}   (optional)')
        self.model_args_edit.setToolTip(
            "Optional argv prefix used when the role has a model configured. "
            "The whole token {model} is replaced by the configured model name."
        )

        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 7200.0)
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(900.0)
        self.timeout_spin.setToolTip(
            "Wall-clock budget for ONE prompt. On expiry the whole process "
            "tree is terminated and the run is reported as failed."
        )

        self.env_edit = QLineEdit()
        self.env_edit.setPlaceholderText("e.g. NO_COLOR=1 API_BASE=https://…  (optional, max 16)")
        self.env_edit.setToolTip(
            "Optional KEY=VALUE environment overrides applied on top of the "
            "standard filtered child environment (supervisor scope never "
            "leaks). Values are never exported or logged."
        )

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: palette(mid);")

        form = QFormLayout()
        form.addRow("Executable", self.executable_edit)
        form.addRow("Arguments (tokens)", self.args_edit)
        form.addRow("Prompt transport", self.transport_combo)
        form.addRow("Result mode", self.result_combo)
        form.addRow("Result field", self.result_field_edit)
        form.addRow("Model arguments", self.model_args_edit)
        form.addRow("Timeout", self.timeout_spin)
        form.addRow("Env overrides", self.env_edit)
        form.addRow("", self.status_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        note = QLabel(
            "Structured configuration only — this is never run through a shell. "
            "Stored in the role configuration; export redacts env override values."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        # -- behaviour wiring -------------------------------------------------
        self.result_combo.currentIndexChanged.connect(self._update_result_field_state)
        self.transport_combo.currentIndexChanged.connect(self._update_status)
        self.args_edit.textChanged.connect(self._update_status)
        self.executable_edit.textChanged.connect(self._update_status)
        self._update_result_field_state()

        self.load(config_dict)

    # -- data ----------------------------------------------------------------
    def load(self, config_dict: dict | None) -> None:
        """Populate the widgets from a stored config dict (or defaults)."""
        data = dict(config_dict or {})
        self.executable_edit.setText(str(data.get("executable") or ""))
        args = data.get("args") or []
        self.args_edit.setPlainText(self._format_tokens([str(a) for a in args]))
        model_args = data.get("model_args") or []
        self.model_args_edit.setText(self._format_tokens([str(a) for a in model_args]))
        transport = str(data.get("prompt_transport") or "stdin")
        index = self.transport_combo.findData(transport)
        self.transport_combo.setCurrentIndex(index if index >= 0 else 0)
        result_mode = str(data.get("result_mode") or "stdout_text")
        index = self.result_combo.findData(result_mode)
        self.result_combo.setCurrentIndex(index if index >= 0 else 0)
        self.result_field_edit.setText(str(data.get("result_field") or ""))
        try:
            self.timeout_spin.setValue(float(data.get("timeout_s") or 900.0))
        except (TypeError, ValueError):
            self.timeout_spin.setValue(900.0)
        env = data.get("env_overrides") or {}
        self.env_edit.setText(
            " ".join(f"{key}={value}" for key, value in env.items())
        )
        self._update_result_field_state()
        self._update_status()

    def _format_tokens(self, tokens: list[str]) -> str:
        """Render argv tokens for editing (shlex.quote tokens with spaces)."""
        return " ".join(shlex.quote(token) for token in tokens)

    def _parse_tokens(self, text: str) -> list[str]:
        """Parse the editor text back into literal argv tokens.

        POSIX-mode ``shlex.split``: quotes group tokens, backslashes are
        ordinary characters, and no substitution of any kind happens.  The
        result are the literal argv members.
        """
        return shlex.split(text, posix=True)

    def build_config_dict(self) -> dict:
        """Parse + fully validate the widgets into a config dict.

        Raises :class:`GenericCliConfigError` with an operator-actionable
        message when anything is invalid.  Token splitting errors are mapped
        to the same failure class.
        """
        executable = self.executable_edit.text().strip()
        try:
            args = self._parse_tokens(self.args_edit.toPlainText())
            model_args = self._parse_tokens(self.model_args_edit.text())
        except ValueError as exc:
            raise GenericCliConfigError(
                f"The arguments field is not a valid token list: {exc}. "
                'Quote tokens containing spaces, e.g. --prompt "two words".'
            ) from None

        env: dict[str, str] = {}
        raw_env = self.env_edit.text().strip()
        if raw_env:
            for chunk in raw_env.split():
                if "=" not in chunk:
                    raise GenericCliConfigError(
                        f"Env override {chunk!r} is not KEY=VALUE."
                    )
                key, _, value = chunk.partition("=")
                env[key] = value

        # Validate through the REAL config class (single validation authority).
        config = GenericCliConfig(
            executable=executable,
            args=args,
            prompt_transport=str(self.transport_combo.currentData() or "stdin"),
            result_mode=str(self.result_combo.currentData() or "stdout_text"),
            result_field=self.result_field_edit.text().strip(),
            model_args=model_args,
            timeout_s=self.timeout_spin.value(),
            env_overrides=env,
        )
        return config.to_dict()

    # -- status ---------------------------------------------------------------
    def _update_result_field_state(self) -> None:
        mode = str(self.result_combo.currentData() or "stdout_text")
        self.result_field_edit.setEnabled(mode in ("json", "jsonl"))
        self._update_status()

    def _update_status(self, *_args: Any) -> None:
        text = self.executable_edit.text().strip()
        if not text:
            self.status_label.setText("Executable required (name on PATH or absolute path).")
            return
        args_text = self.args_edit.toPlainText()
        if len(args_text) > self._MAX_TOKEN_CHARS:
            self.status_label.setText("Arguments exceed the size bound.")
            return
        try:
            self._parse_tokens(args_text)
        except ValueError as exc:
            self.status_label.setText(f"Unbalanced quoting in arguments: {exc}")
            return
        self.status_label.setText(
            f"Executable: {text}. Tokens are passed literally to the process "
            "(no shell). The configuration is fully validated when you press Save."
        )

    # -- Qt override ------------------------------------------------------------
    def accept(self) -> None:  # noqa: D102 - QDialog API
        try:
            self.build_config_dict()
        except GenericCliConfigError as exc:
            self.status_label.setText(f"Not saved — {exc}")
            self.status_label.setStyleSheet("color: palette(bright-text);")
            return
        self.status_label.setStyleSheet("color: palette(mid);")
        super().accept()
