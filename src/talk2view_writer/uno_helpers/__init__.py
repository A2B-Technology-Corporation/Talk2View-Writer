"""Low-level UNO helpers shared by the tools modules.

Splits the awkward XText/XTable/XStyle/Annotation APIs into small,
testable functions that tools can call without each tool re-implementing
cursor handling, range conversion, etc.
"""
