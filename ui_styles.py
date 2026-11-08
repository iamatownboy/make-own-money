"""Shared styles for the Streamlit screens."""

from pathlib import Path

import streamlit as st


STYLE_DIR = Path(__file__).resolve().parent / "styles"


def render_styles(*names: str) -> None:
    css = "\n".join((STYLE_DIR / name).read_text(encoding="utf-8") for name in names)
    st.html(f"<style>{css}</style>")
