"""Inspect specific resource-ids in current UI dump file."""
import sys
import re
from xml.etree import ElementTree as ET

path = sys.argv[1]
ids_to_check = sys.argv[2:]

tree = ET.parse(path)
for rid in ids_to_check:
    print(f"\n=== {rid} ===")
    found = False
    for node in tree.iter("node"):
        if node.get("resource-id", "") == rid:
            found = True
            t = node.get("text", "")
            d = node.get("content-desc", "")
            cls = node.get("class", "")
            b = node.get("bounds", "")
            print(f"  cls={cls}")
            print(f"  text={t!r}")
            print(f"  desc={d!r}")
            print(f"  bounds={b}")
            # children
            for ch in list(node):
                ct = ch.get("text", "")
                cd = ch.get("content-desc", "")
                cc = ch.get("class", "")
                cr = ch.get("resource-id", "")
                print(f"    child cls={cc} rid={cr} text={ct!r} desc={cd!r}")
    if not found:
        print("  NOT FOUND")

