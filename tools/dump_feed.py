"""Open IG, go to feed, click a post-like area then dump."""
import time
import sys
import uiautomator2 as u2

dev = u2.connect()
print("Current:", dev.app_current())

# Vai al feed
dev(resourceId="com.instagram.android:id/feed_tab").click()
time.sleep(3)
out = sys.argv[1] if len(sys.argv) > 1 else "ui_feed.xml"
xml = dev.dump_hierarchy()
with open(out, "w", encoding="utf-8") as f:
    f.write(xml)
print(f"WROTE {out} ({len(xml)} bytes)")

