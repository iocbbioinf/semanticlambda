#!/usr/bin/env python3
"""Knowledge Graph Browser — terminal app for exploring the AHoJ RDF knowledge graph."""
from app import KGBrowser

if __name__ == "__main__":
    app = KGBrowser()
    app.run(mouse=False)
