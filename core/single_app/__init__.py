# coding: utf-8
"""Single-program user-facing CapsWriter application."""


def __getattr__(name):
    if name == "CapsWriterSingleApp":
        from .app import CapsWriterSingleApp

        return CapsWriterSingleApp
    raise AttributeError(name)

__all__ = ["CapsWriterSingleApp"]
