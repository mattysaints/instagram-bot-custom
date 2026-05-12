"""Aperture IG e dump per ispezione like/media."""
import time
import uiautomator2 as u2

dev = u2.connect()
print("App:", dev.app_current())

# clicca feed tab per assicurarsi
try:
    dev(resourceId="com.instagram.android:id/feed_tab").click()
    time.sleep(3)
except Exception as e:
    print("err:", e)

# dump
xml = dev.dump_hierarchy()
with open("ui_feed2.xml", "w", encoding="utf-8") as f:
    f.write(xml)
print(f"WROTE ui_feed2.xml ({len(xml)} bytes)")

# inspect media_group nodes
from xml.etree import ElementTree as ET
root = ET.fromstring(xml)
for node in root.iter("node"):
    rid = node.get("resource-id", "")
    if rid in (
        "com.instagram.android:id/media_group",
        "com.instagram.android:id/video_container",
        "com.instagram.android:id/row_feed_button_like",
        "com.instagram.android:id/carousel_image",
        "com.instagram.android:id/carousel_image_media_group",
        "com.instagram.android:id/carousel_video_media_group",
        "com.instagram.android:id/row_feed_photo_profile_name",
        "com.instagram.android:id/row_feed_button_comment",
    ):
        print(f"\n--- {rid}")
        print(f"  class={node.get('class')}")
        print(f"  text={node.get('text')!r}")
        print(f"  desc={node.get('content-desc')!r}")
        print(f"  bounds={node.get('bounds')}")
        print(f"  clickable={node.get('clickable')} selected={node.get('selected')}")

