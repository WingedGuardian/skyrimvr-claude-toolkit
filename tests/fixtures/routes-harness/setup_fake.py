import os
B = "C:/Temp/rt-routes"
for d in ["Game/Data", "Game/.claude", "Game/tools", "cfg/Saves", "scratch", "Game/Data/Scripts/Source"]:
    os.makedirs(B + "/" + d, exist_ok=True)
for f in ["Game/Data/Plugin.esp", "Game/Data/Skyrim.esm", "cfg/Saves/Existing.ess", "Game/SkyrimVR.exe"]:
    open(B + "/" + f, "wb").write(b"x")
open(B + "/Game/skyrim-paths.env", "w").write(          # Plan 8 T8: 4.0 writes it at the toolkit root
    'SKYRIM_GAME_ROOT="C:/Temp/rt-routes/Game"\nSKYRIM_CONFIG_DIR="C:/Temp/rt-routes/cfg"\n')
