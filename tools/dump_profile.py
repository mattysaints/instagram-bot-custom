"""Navigate to profile tab and dump UI hierarchy."""
import sys
import time
import uiautomator2 as u2

dev = u2.connect()
print("Current:", dev.app_current())

# click profile tab
tab = dev(resourceId="com.instagram.android:id/profile_tab")
if tab.exists:
    tab.click()
    print("Clicked profile_tab")
else:
    print("profile_tab not found, trying tab_avatar")
    dev(resourceId="com.instagram.android:id/tab_avatar").click()

time.sleep(4)
xml = dev.dump_hierarchy()
out = sys.argv[1] if len(sys.argv) > 1 else "ui_profile.xml"
with open(out, "w", encoding="utf-8") as f:
    f.write(xml)
print(f"WROTE {out} ({len(xml)} bytes)")

