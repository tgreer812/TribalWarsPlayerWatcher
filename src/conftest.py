"""
conftest.py – adds bot/ to sys.path so tests can import bot modules
without requiring an installed package.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bot"))

