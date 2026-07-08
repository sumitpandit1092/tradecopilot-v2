# FIXED: this used to eagerly do `from .execution_engine import
# ExecutionEngine`, which forces the tvDatafeed dependency to be
# importable any time ANY services module is touched -- even pure
# logic modules like structure_engine.py or fvg.py that have nothing
# to do with market data or tvDatafeed at all. That made it
# impossible to unit-test the math in isolation without a working
# TradingView connection installed.
#
# Nothing in this project actually relies on `from services import
# ExecutionEngine` (everything already imports
# `from services.execution_engine import ExecutionEngine` directly),
# so this file is intentionally left empty.
