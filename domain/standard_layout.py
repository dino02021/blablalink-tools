"""Shared twelve-column standard template identities."""

METRICS = ("breakthrough", "skill_1", "skill_2", "skill_burst", "equip_head", "equip_arm", "equip_torso", "equip_leg", "collection")
GROUPS = ((0, 1, "突破"), (1, 4, "技能"), (4, 8, "裝備 (頭/手/身/腳)"), (8, 9, "收藏品"), (9, 12, "詞條"))
ABBREVIATIONS = {"優越代碼傷害": "優", "攻擊力": "攻", "最大裝彈數": "彈", "蓄力速度": "速",
                 "暴擊率": "暴率", "暴擊傷害": "暴傷", "蓄力傷害": "蓄傷", "命中率": "命", "防禦力": "防"}
