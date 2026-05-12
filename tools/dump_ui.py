"""Dump UI hierarchy via uiautomator2 (atx-agent already installed)."""
import sys
import uiautomator2 as u2

dev = u2.connect()
print("CONNECTED:", dev.info.get("productName"), dev.info.get("displayWidth"), "x", dev.info.get("displayHeight"))
print("CURRENT APP:", dev.app_current())
xml = dev.dump_hierarchy()
out = sys.argv[1] if len(sys.argv) > 1 else "ui_dump.xml"
with open(out, "w", encoding="utf-8") as f:
    f.write(xml)
print(f"WROTE {out} ({len(xml)} bytes)")

