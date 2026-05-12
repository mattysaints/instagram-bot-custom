"""Scorri il feed di un po' e dumpa - cerca post con content-desc."""
import time
import uiautomator2 as u2
from xml.etree import ElementTree as ET

dev = u2.connect()
W, H = dev.info["displayWidth"], dev.info["displayHeight"]

for i in range(5):
    xml = dev.dump_hierarchy()
    root = ET.fromstring(xml)
    print(f"\n========== SCROLL {i} ==========")
    for node in root.iter("node"):
        rid = node.get("resource-id", "")
        if rid in (
            "com.instagram.android:id/media_group",
            "com.instagram.android:id/video_container",
            "com.instagram.android:id/carousel_image",
            "com.instagram.android:id/carousel_image_media_group",
        ):
            print(f"  {rid}  cls={node.get('class')}  text={node.get('text')!r}  desc={node.get('content-desc')!r}  bounds={node.get('bounds')}  clickable={node.get('clickable')}")
    # scroll giù
    dev.swipe(W/2, H*0.7, W/2, H*0.3, duration=0.4)
    time.sleep(2.5)

# salva ultimo dump
with open("ui_feed_scroll.xml", "w", encoding="utf-8") as f:
    f.write(dev.dump_hierarchy())
print("\nSAVED ui_feed_scroll.xml")

