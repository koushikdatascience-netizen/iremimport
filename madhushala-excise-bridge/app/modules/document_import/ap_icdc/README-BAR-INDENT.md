# AP Bar Indent — separate document type

The two-page `andra1.pdf` contains **17** product lines, an 11-column item
table, Bar Indent/Indent Number, product codes and brand codes, Issue Price
and Amount. It does NOT share the 10-column APSBCL ICDC financial layout.

The parser in `bar_indent.py` is separate and intentionally not wired
to Purchase Save. The Indent is typically an ordering document; the team
must confirm whether it should produce a Purchase transaction, create an
Indent staging record, or require the paired ICDC. **Do not count the same
order twice.**

Expected fixture: 17 rows; 113 cases; printed Issue Price 360631.00.
The code has not undergone a complete real-file OCR accuracy benchmark.
