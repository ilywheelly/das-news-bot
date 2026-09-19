DAS Devanagari database builder

1. Put build_devanagari.py in the DAS project folder.
2. Run:
   python3 build_devanagari.py
3. It creates:
   devanagari.json

The resulting database contains only Devanagari text used for lookup:
- BG keys: chapter.verse, e.g. BG["18.65"]
- SB keys: canto.chapter.verse, e.g. SB["10.14.8"]

The bot can then use sridhar.guru as the semantic/source archive and this local JSON
only to add the original Devanagari spelling for Sanskrit verses.
