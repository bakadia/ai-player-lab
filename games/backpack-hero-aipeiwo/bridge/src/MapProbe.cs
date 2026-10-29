// 地图（mode == "map"）与「非战斗等待界面」的只读探针。
//
// 背景（2026-10-26 对 HANDOFF §1 的复核）：
//   原计划假设「地图节点就是 EventButton」。静态类型地图不支持这个假设：
//   EventButton 的字段是 requirement(goldCost / itemSacrifice / itemSelect…)、
//   requiredGold、possibleOutcomes、PayCosts()、ShowButtons()、PerfromEffect()，
//   这是**事件对话框里的选项按钮**，不是地图节点。
//   地图看起来是「房间网格 + 房间里的 DungeonEvent + 门 + 可走动的玩家」：
//     DungeonPlayer.main.room / DungeonRoom.allRooms / DungeonEvent.dungeonEvents / Door
// 所以这里四类对象一起摊平，同时把 EventButton 也数一遍——
// 哪套模型对，交给实测数据，不靠猜。
//
// 四条硬约束（与 StateReader 一致）：
//   1. 只读：不点、不调协程、不写游戏状态。
//   2. 每个字段单独兜底：读不到就输出哨兵，不能让整份 state 废掉。
//   3. 先取局部变量，再写 JSON。
//   4. C# 5：不能用字符串插值、?.、表达式体成员、nameof、元组、out var。

using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;

namespace BphBridge
{
    internal static class MapProbe
    {
        private const int MaxRooms = 60;
        private const int MaxEvents = 40;
        private const int MaxDoors = 16;
        private const int MaxButtons = 8;
        private const int MaxProps = 12;

        // DungeonEvent+DungeonEventType，索引即枚举值。
        private static readonly string[] EventTypeNames = new string[]
        {
            "Enemy", "Elite", "Chest", "Store", "Chance", "Lock", "Exit", "Healer",
            "EventNoNPC", "ExitAlternate", "Shambler", "SimpleClickItem", "CaveIn",
            "Altar", "Burrow", "Parcel"
        };

        // DungeonLevel+Zone
        private static readonly string[] ZoneNames = new string[]
        {
            "dungeon", "deepCave", "magmaCore", "EnchantedSwamp", "theBramble", "ice", "Chaos"
        };

        // DungeonLevel+Floor
        private static readonly string[] FloorNames = new string[]
        {
            "all", "intro", "first", "second", "third", "boss"
        };

        // DungeonEvent+EventProperty+Type
        private static readonly string[] PropTypeNames = new string[]
        {
            "finished", "cost", "boughtItems", "mimic", "randomSeed",
            "SimpleClickItem", "open", "increaseCost", "selectedType"
        };

        // GameManager+InventoryPhase
        private static readonly string[] InventoryPhaseNames = new string[]
        {
            "locked", "open", "choose", "specialReorganization", "dead",
            "inCombatMove", "clickItem", "placePet", "notInteractable"
        };

        // GameManager+ViewingTop
        private static readonly string[] ViewingTopNames = new string[]
        {
            "inventory", "map"
        };

        // TutorialManager+TutorialSequence（索引即枚举值）
        private static readonly string[] TutorialSeqNames = new string[]
        {
            "start", "openingMessage", "moveToChest", "organizeChest", "openMap",
            "moveToCombat", "firstCombat", "done", "trulyDone",
            "cr8start", "cr8battle1", "cr8battle2", "cr8battle3", "cr8battle4",
            "cr8battle5", "cr8battle6", "cr8battle7", "cr8battle8",
            "toteStart", "toteBattle1", "toteBattle2", "toteBattle3", "toteBattle4",
            "toteChest", "toteChestOpen", "purseAfterOpenMap", "cr8battle9"
        };

        private static readonly Dictionary<string, FieldInfo> FieldCache =
            new Dictionary<string, FieldInfo>();

        // ---------- 取值兜底 ----------

        private static int IInt(Func<int> f, int fallback)
        {
            try { return f(); } catch { return fallback; }
        }

        private static bool BBool(Func<bool> f, bool fallback)
        {
            try { return f(); } catch { return fallback; }
        }

        private static string SStr(Func<string> f)
        {
            try { string v = f(); return v == null ? "" : v; } catch { return ""; }
        }

        private static string Pick(string[] names, int index)
        {
            if (names == null || index < 0 || index >= names.Length) return "";
            return names[index];
        }

        private static float R1(float v)
        {
            return (float)Math.Round((double)v, 1);
        }

        /// <summary>
        /// 读 nonpublic 字段（DungeonRoom.dungeonEvent / Door.isLocked / …）。
        /// 只读 GetValue，不写。
        /// </summary>
        private static object Field(object obj, string name)
        {
            if (obj == null) return null;
            try
            {
                Type t = obj.GetType();
                string key = t.FullName + "|" + name;
                FieldInfo fi;
                if (!FieldCache.TryGetValue(key, out fi))
                {
                    // 必须自己往上走基类：GetField 找不到基类的 private 字段。
                    // 实测 The Bramble 的房间里 dungeonEvent 全是 null，
                    // 而序章（普通 DungeonRoom）正常 —— 很可能就是子类导致的。
                    fi = null;
                    Type cur = t;
                    while (cur != null && fi == null)
                    {
                        fi = cur.GetField(name,
                            BindingFlags.Instance | BindingFlags.Public |
                            BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
                        cur = cur.BaseType;
                    }
                    FieldCache[key] = fi;
                }
                if (fi == null) return null;
                return fi.GetValue(obj);
            }
            catch { return null; }
        }

        private static bool BField(object obj, string name, bool fallback)
        {
            object v = Field(obj, name);
            if (v is bool) return (bool)v;
            return fallback;
        }

        private static int IField(object obj, string name, int fallback)
        {
            object v = Field(obj, name);
            if (v is int) return (int)v;
            return fallback;
        }

        // ---------- 入口 ----------

        /// <summary>
        /// 写 "map" 与 "ui" 两段。detail（房间/事件/门全量）只在 mode=="map" 时展开，
        /// 避免战斗期间把 state.json 撑大、并在每帧移动时反复触发重写。
        /// </summary>
        public static void Write(JsonWriter w, string mode, GameManager gm)
        {
            DungeonPlayer dp = null;
            try { dp = DungeonPlayer.main; } catch { }

            bool active = dp != null;
            // 注意：detail 不能挂在 mode=="map" 上。
            // 教程/序章的 floor 还是 0（mode=intro），实测整段教程因此没有地图细节。
            // 只要不在战斗里、地牢对象在，就展开。
            bool inCombat = mode == "battle" || mode == "reorganize";
            bool detail = active && !inCombat;

            w.Name("map").BeginObject();
            w.Prop("active", active);
            w.Prop("detail", detail);
            WriteZone(w, gm);

            if (detail)
            {
                WriteMapRoot(w);
                WritePlayer(w, dp);
                WriteRooms(w, dp);
                WriteEvents(w);
                WriteDoors(w);
                WriteButtons(w);
            }
            w.EndObject();

            WriteMenu(w);
            WriteUi(w, gm);
        }

        // ---------- 主菜单 ----------
        // 开局脚本要判断「现在停在哪个界面」，光看 mode=menu 分不出
        // 主菜单 / 角色选择。这两个对象在就说明对应界面开着。

        private static void WriteMenu(JsonWriter w)
        {
            bool hasMenu = false;
            bool hasSelector = false;

            try { hasMenu = MenuManager.main != null; } catch { }
            try
            {
                UnityEngine.Object[] objs =
                    UnityEngine.Object.FindObjectsOfType(typeof(NewCharacterSelector));
                hasSelector = objs != null && objs.Length > 0;
            }
            catch { }

            w.Name("menu").BeginObject();
            w.Prop("menu_manager", hasMenu);
            w.Prop("character_selector", hasSelector);
            w.EndObject();
        }

        // ---------- 地图阶段的合法动作 ----------
        // 只描述「现在能做什么」，不做选择；选择留给 agent。
        // 顺序有意义：弹窗/事件/升级 都在「移动」之前，因为它们会挡住移动。

        public static void WriteLegal(JsonWriter w)
        {
            DungeonPlayer dp = null;
            try { dp = DungeonPlayer.main; } catch { }
            if (dp == null) return;

            GameManager gm = null;
            try { gm = GameManager.main; } catch { }

            int viewing = -1;
            try { if (gm != null) viewing = (int)gm.viewingTop; } catch { }

            // 0) 升级界面开着的时候，别的都别做——先把升级收尾。
            //    实测点完「升级!」之后 levelingUp=true、inventoryPhase=0(locked)，
            //    此时移动/开地图都没意义。
            LevelUpManager lumCheck = null;
            try { lumCheck = LevelUpManager.main; } catch { }
            if (lumCheck != null && BBool(delegate { return lumCheck.levelingUp; }, false))
            {
                w.BeginObject();
                w.Prop("type", "finish_level_up");
                w.EndObject();
                return;
            }

            // 1) 有弹窗先关（不关，别的都点不动）
            if (World.CountPopups() > 0)
            {
                w.BeginObject();
                w.Prop("type", "close_popup");
                w.EndObject();
            }

            // 2) 当前房间里有事件 -> 可以触发（Exit/Chest/Enemy 都是这个入口）
            //    已结束的事件不给：实测打死的敌人事件还留在列表里（finished=true），
            //    不筛掉的话 agent 会对着尸体再触发一次。
            DungeonEvent ev = null;
            try { ev = dp.dungeonEvent; } catch { }
            bool evFinished = ev == null ? true : BBool(delegate { return ev.IsFinished(); }, true);
            if (ev != null && !evFinished)
            {
                int etype = IInt(delegate { return (int)ev.dungeonEventType; }, -1);
                w.BeginObject();
                w.Prop("type", "start_event");
                WriteRef(w, "event", "d", ev);
                w.Prop("event_type", etype);
                w.Prop("event_type_name", Pick(EventTypeNames, etype));
                w.EndObject();

                // 2.2) 宝箱：StartEvent 对 Chest 是 no-op（真机 + IL 都确认），
                //      真正入口是 Chest.OpenChest()。只有房间里确实有未开的 Chest 才给。
                if (etype == 2 && Actions.HasClosedChestInRoom())
                {
                    w.BeginObject();
                    w.Prop("type", "open_chest");
                    WriteRef(w, "event", "d", ev);
                    w.Prop("event_type", etype);
                    w.Prop("event_type_name", Pick(EventTypeNames, etype));
                    w.EndObject();
                }

                // 2.5) 出口/传送门：真正触发点是 Door（StartEvent 实测无效）
                // DungeonEventType: Exit=6, ExitAlternate=9
                if (etype == 6 || etype == 9)
                {
                    List<Door> doors = World.AllDoors();
                    if (doors != null)
                    {
                        for (int i = 0; i < doors.Count && i < 4; i++)
                        {
                            Door d = doors[i];
                            if (d == null) continue;
                            w.BeginObject();
                            w.Prop("type", "enter_door");
                            WriteRef(w, "door", "o", d);
                            w.Prop("open", BBool(delegate { return d.isOpen; }, false));
                            w.EndObject();
                        }
                    }
                }
            }

            // 2.9) 背包容量 + 地上的战利品。
            //
            // 旧逻辑在 `free == 0` 时**什么都不给**，于是「满包」就完全看不见战利品，
            // agent 也就没有机会腾位（drop/move/rotate）。现在不管满不满都列，
            // 带上 `needs`（这件要占几格）和 `free`，让上层自己决定要不要腾。
            //
            // 腾位候选（只在「确实放不下」时才给，避免 legal 里塞一堆噪音）：
            //   - `drop_item`：每件背包物品一条（带 size / is_relic / gold），上层按价值挑
            //   - `move_item` / `rotate_item`：背包物品各一条（不带 cell，桥接自己找落点），
            //     用来试「挪一挪/转一转能不能拼出连续空间」
            // 这三类只描述「可以做的动作」，具体怎么腾由 agent 的 planner（agent/backpack.py）
            // 用 probe_fit 的几何表算——legal 里不展开 4 朝向 × 所有格子（那会是几百条）。
            WriteLootAndRoom(w);

            // 3) 能升级
            LevelUpManager lum = null;
            try { lum = LevelUpManager.main; } catch { }
            bool canLevel = false;
            if (lum != null) canLevel = BBool(delegate { return lum.CanLevelUp(); }, false);
            if (canLevel)
            {
                w.BeginObject();
                w.Prop("type", "level_up");
                w.EndObject();
            }

            // 4) 地图没打开 -> 可以打开（点右上角地图等价）
            if (viewing != 1)
            {
                w.BeginObject();
                w.Prop("type", "open_map");
                w.EndObject();
            }

            // 5) 可以走过去的房间（看得见、没锁、不是当前房间）
            List<DungeonRoom> rooms = null;
            try { rooms = DungeonRoom.allRooms; } catch { }
            if (rooms == null) return;

            string cur = World.PlayerRoomUid();
            int written = 0;
            for (int i = 0; i < rooms.Count && written < 24; i++)
            {
                DungeonRoom r = rooms[i];
                if (r == null) continue;

                string uid = World.RoomUid(r);
                if (uid.Length == 0 || uid == cur) continue;
                if (!BBool(delegate { return r.canBeSeenOnMap; }, false)) continue;
                if (BBool(delegate { return r.isLocked; }, false)) continue;

                w.BeginObject();
                w.Prop("type", "move_to_room");
                w.Prop("room", uid);
                w.EndObject();
                written++;
            }

            // 6) 地上的战利品已经在 2.9 里统一给了（带 needs/free），这里不再重复。
        }

        /// <summary>
        /// 战利品摆放 + 腾位候选。这一段是「满包也能捡东西」的入口：
        /// 以前 `free == 0` 时一个 place_item 都不给，agent 连尝试的机会都没有。
        /// </summary>
        private static void WriteLootAndRoom(JsonWriter w)
        {
            List<Item2> looseItems = World.ItemsLoose();
            if (looseItems == null || looseItems.Count == 0) return;

            int free = Actions.CountFreeSquares();

            List<Item2> bagItems = World.ItemsInGrid();
            int bagCount = 0;
            if (bagItems != null)
            {
                for (int i = 0; i < bagItems.Count; i++)
                {
                    if (bagItems[i] != null && World.IsPlaced(bagItems[i])) bagCount++;
                }
            }

            bool anyTooBig = false;

            for (int i = 0; i < looseItems.Count && i < 4; i++)
            {
                Item2 lit = looseItems[i];
                if (lit == null) continue;

                int luid = IInt(delegate { return lit.GetInstanceID(); }, 0);
                string lname = SStr(delegate { return lit.displayName; });
                int needs = Actions.SpacesNeeded(lit, null);
                // free >= needs 只是必要条件（碎片化时仍放不下），真正能不能放
                // 由 agent 用 probe_fit 的几何表算；这里给一个便宜的提示。
                bool fitsGuess = needs > 0 && free >= needs;
                if (!fitsGuess) anyTooBig = true;

                w.BeginObject();
                w.Prop("type", "place_item");
                w.Prop("uid", "i" + luid);
                w.Prop("name", lname);
                w.Prop("needs", needs);
                w.Prop("free", free);
                w.Prop("fits", fitsGuess);
                w.EndObject();
            }

            // 有东西放不下 -> 给出「怎么腾」的候选（真的可执行的动作，不是提示）
            if (!anyTooBig || bagCount == 0) return;

            int drops = 0;
            int moves = 0;
            for (int i = 0; i < bagItems.Count && (drops < 4 || moves < 3); i++)
            {
                Item2 it = bagItems[i];
                if (it == null || !World.IsPlaced(it)) continue;

                int iuid = IInt(delegate { return it.GetInstanceID(); }, 0);
                string iname = SStr(delegate { return it.displayName; });
                List<string> cells = Actions.ItemCells(it);
                int size = cells.Count;
                bool relic = BBool(delegate { return Actions.IsRelicItem(it); }, false);
                int gold = IInt(delegate { return Actions.ItemGoldCost(it); }, 0);

                if (drops < 4)
                {
                    w.BeginObject();
                    w.Prop("type", "drop_item");
                    w.Prop("uid", "i" + iuid);
                    w.Prop("name", iname);
                    w.Prop("size", size);
                    w.Prop("is_relic", relic);
                    w.Prop("gold", gold);
                    w.Prop("why", "make_room");
                    w.EndObject();
                    drops++;
                }

                if (moves < 3)
                {
                    w.BeginObject();
                    w.Prop("type", "move_item");
                    w.Prop("uid", "i" + iuid);
                    w.Prop("name", iname);
                    w.Prop("size", size);
                    w.Prop("why", "make_room");
                    w.EndObject();

                    w.BeginObject();
                    w.Prop("type", "rotate_item");
                    w.Prop("uid", "i" + iuid);
                    w.Prop("name", iname);
                    w.Prop("times", 1);
                    w.Prop("why", "make_room");
                    w.EndObject();
                    moves++;
                }
            }
        }

        // ---------- 地图根（原点 / 模式） ----------
        // 地图会跟着玩家平移：实测两次读数之间所有坐标统一平移了 (-25, 0)。
        // 所以绝对坐标不能跨时刻比较，得先记下 MapManager.map 的位置当原点。

        private static void WriteMapRoot(JsonWriter w)
        {
            UnityEngine.Object[] objs = null;
            try { objs = UnityEngine.Object.FindObjectsOfType(typeof(MapManager)); } catch { }

            bool hasOrigin = false;
            float ox = 0f, oy = 0f;
            int mode = -1;

            if (objs != null && objs.Length > 0)
            {
                MapManager mm = objs[0] as MapManager;
                if (mm != null)
                {
                    mode = IInt(delegate { return (int)mm.mapMode; }, -1);
                    try
                    {
                        Transform t = Field(mm, "map") as Transform;
                        if (t != null)
                        {
                            Vector3 p = t.position;
                            ox = p.x; oy = p.y; hasOrigin = true;
                        }
                    }
                    catch { }
                }
            }

            WriteVec(w, "origin", hasOrigin, ox, oy);
            w.Prop("map_mode", mode);
        }

        // ---------- 区域 / 楼层 ----------

        private static void WriteZone(JsonWriter w, GameManager gm)
        {
            int zoneNumber = -1;
            string area = "";
            int zone = -1;
            int floorIdx = -1;

            try { if (gm != null) zoneNumber = gm.zoneNumber; } catch { }
            try
            {
                DungeonLevel dl = gm == null ? null : gm.dungeonLevel;
                if (dl != null)
                {
                    area = SStr(delegate { return dl.areaName; });
                    zone = IInt(delegate { return (int)dl.zone; }, -1);
                    floorIdx = IInt(delegate { return (int)dl.currentFloor; }, -1);
                }
            }
            catch { }

            w.Prop("zone_number", zoneNumber);
            w.Prop("zone", zone);
            w.Prop("zone_name", Pick(ZoneNames, zone));
            w.Prop("area", area);
            w.Prop("dungeon_floor", floorIdx);
            w.Prop("dungeon_floor_name", Pick(FloorNames, floorIdx));
        }

        // ---------- 玩家 ----------

        private static void WritePlayer(JsonWriter w, DungeonPlayer dp)
        {
            DungeonRoom room = null;
            DungeonEvent ev = null;
            try { room = dp.room; } catch { }
            try { ev = dp.dungeonEvent; } catch { }

            bool hasSpace = false;
            float sx = 0f, sy = 0f;
            try
            {
                Vector2 v = dp.lastSpace;
                sx = v.x; sy = v.y; hasSpace = true;
            }
            catch { }

            // room 这个字段实测一直是 null（走路不赋值），所以再按世界坐标反查一次。
            bool hasPos = false;
            float px = 0f, py = 0f;
            try
            {
                Transform t = dp.transform;
                if (t != null)
                {
                    Vector3 p = t.position;
                    px = p.x; py = p.y; hasPos = true;
                }
            }
            catch { }

            // 游戏自带的 GetRoom() 在刚切层时对「坐标完全相等」的房间也返回 null，
            // 改成自己按距离找（SPIKE-06 §15）。
            DungeonRoom at = null;
            float dist = -1f;
            if (hasPos)
            {
                try
                {
                    dist = float.MaxValue;
                    at = World.NearestRoom(new Vector3(px, py, 0f), World.RoomSnapDistance, out dist);
                    if (at == null) dist = -1f;
                }
                catch { }
            }

            w.Name("player").BeginObject();
            WriteRef(w, "room", "r", room);
            WriteRef(w, "room_at", "r", at);
            w.Prop("room_dist", dist < 0f ? -1f : R1(dist));
            WriteRef(w, "event", "d", ev);
            w.Prop("moving", BBool(delegate { return dp.isMoving; }, false));
            w.Prop("locked", BBool(delegate { return dp.locked; }, false));
            w.Prop("from_locked_door", BBool(delegate { return dp.isMovingFromLockedDoor; }, false));
            WriteVec(w, "space", hasSpace, sx, sy);
            WriteVec(w, "pos", hasPos, px, py);
            w.EndObject();
        }

        // ---------- 房间 ----------

        private static void WriteRooms(JsonWriter w, DungeonPlayer dp)
        {
            List<DungeonRoom> rooms = null;
            try { rooms = DungeonRoom.allRooms; } catch { }

            DungeonRoom current = null;
            int currentId = -1;
            try { current = dp == null ? null : dp.room; } catch { }
            if (current != null) currentId = IInt(delegate { return current.GetInstanceID(); }, -1);

            int total = rooms == null ? -1 : rooms.Count;
            int written = 0;

            w.Name("rooms").BeginArray();
            if (rooms != null)
            {
                for (int i = 0; i < rooms.Count; i++)
                {
                    DungeonRoom r = rooms[i];
                    if (r == null) continue;
                    if (written >= MaxRooms) continue;

                    // 局部变量先取出来，再写（捕获的是每轮独立变量）。
                    int rid = IInt(delegate { return r.GetInstanceID(); }, 0);
                    bool locked = BBool(delegate { return r.isLocked; }, false);
                    bool visible = BBool(delegate { return r.canBeSeenOnMap; }, false);
                    bool dl = BBool(delegate { return r.left; }, false);
                    bool dr = BBool(delegate { return r.right; }, false);
                    bool du = BBool(delegate { return r.up; }, false);
                    bool dd = BBool(delegate { return r.down; }, false);
                    bool bl = BBool(delegate { return r.leftBlocked; }, false);
                    bool br = BBool(delegate { return r.rightBlocked; }, false);
                    bool bu = BBool(delegate { return r.upBlocked; }, false);
                    bool bd = BBool(delegate { return r.downBlocked; }, false);

                    bool hasPos = false;
                    float px = 0f, py = 0f;
                    try
                    {
                        Transform tr = r.transform;
                        if (tr != null)
                        {
                            Vector3 p = tr.position;
                            px = p.x; py = p.y; hasPos = true;
                        }
                    }
                    catch { }

                    DungeonEvent rev = null;
                    try { rev = Field(r, "dungeonEvent") as DungeonEvent; } catch { }

                    w.BeginObject();
                    w.Prop("uid", "r" + rid);
                    w.Prop("is_player", currentId >= 0 && rid == currentId);
                    w.Prop("locked", locked);
                    w.Prop("visible", visible);
                    w.Name("doors").BeginObject();
                    w.Prop("left", dl);
                    w.Prop("right", dr);
                    w.Prop("up", du);
                    w.Prop("down", dd);
                    w.EndObject();
                    w.Name("blocked").BeginObject();
                    w.Prop("left", bl);
                    w.Prop("right", br);
                    w.Prop("up", bu);
                    w.Prop("down", bd);
                    w.EndObject();
                    WriteRef(w, "event", "d", rev);
                    WriteVec(w, "pos", hasPos, px, py);
                    w.EndObject();

                    written++;
                }
            }
            w.EndArray();
            w.Prop("rooms_total", total);
        }

        // ---------- 地牢事件（真正的地图「节点」） ----------

        private static void WriteEvents(JsonWriter w)
        {
            List<DungeonEvent> list = null;
            try { list = DungeonEvent.FindAllDungeonEvents(); } catch { }
            if (list == null) { try { list = DungeonEvent.dungeonEvents; } catch { } }

            int total = list == null ? -1 : list.Count;
            int written = 0;

            w.Name("events").BeginArray();
            if (list != null)
            {
                for (int i = 0; i < list.Count; i++)
                {
                    DungeonEvent e = list[i];
                    if (e == null) continue;
                    if (written >= MaxEvents) continue;

                    int eid = IInt(delegate { return e.GetInstanceID(); }, 0);
                    int type = IInt(delegate { return (int)e.dungeonEventType; }, -1);
                    bool finished = BBool(delegate { return e.IsFinished(); }, false);
                    bool passable = BBool(delegate { return e.passable; }, false);
                    bool noWalkAway = BBool(delegate { return e.cannotWalkAwayFrom; }, false);
                    int door = IInt(delegate { return e.doorNumber; }, -1);
                    int expires = IInt(delegate { return e.turnsToExpire; }, -1);
                    string textKey = SStr(delegate { return e.mapTextOverrideKey; });

                    bool hasPos = false;
                    float px = 0f, py = 0f;
                    try
                    {
                        Transform tr = e.transform;
                        if (tr != null)
                        {
                            Vector3 p = tr.position;
                            px = p.x; py = p.y; hasPos = true;
                        }
                    }
                    catch { }

                    w.BeginObject();
                    w.Prop("uid", "d" + eid);
                    w.Prop("type", type);
                    w.Prop("type_name", Pick(EventTypeNames, type));
                    w.Prop("finished", finished);
                    w.Prop("passable", passable);
                    w.Prop("no_walk_away", noWalkAway);
                    w.Prop("door_number", door);
                    w.Prop("turns_to_expire", expires);
                    w.Prop("text_key", textKey);
                    WriteVec(w, "pos", hasPos, px, py);
                    WriteEventProps(w, e);
                    w.EndObject();

                    written++;
                }
            }
            w.EndArray();
            w.Prop("events_total", total);
        }

        private static void WriteEventProps(JsonWriter w, DungeonEvent e)
        {
            List<DungeonEvent.EventProperty> props = null;
            try { props = e.eventProperties; } catch { }

            w.Name("props").BeginArray();
            if (props != null)
            {
                for (int k = 0; k < props.Count && k < MaxProps; k++)
                {
                    DungeonEvent.EventProperty p = props[k];
                    if (p == null) continue;

                    int pt = IInt(delegate { return (int)p.type; }, -1);
                    int pv = IInt(delegate { return p.value; }, -1);

                    w.BeginObject();
                    w.Prop("type", pt);
                    w.Prop("name", Pick(PropTypeNames, pt));
                    w.Prop("value", pv);
                    w.EndObject();
                }
            }
            w.EndArray();
        }

        // ---------- 门 ----------

        private static void WriteDoors(JsonWriter w)
        {
            UnityEngine.Object[] objs = null;
            try { objs = UnityEngine.Object.FindObjectsOfType(typeof(Door)); } catch { }

            int total = objs == null ? -1 : objs.Length;
            int written = 0;

            w.Name("doors").BeginArray();
            if (objs != null)
            {
                for (int i = 0; i < objs.Length; i++)
                {
                    Door d = objs[i] as Door;
                    if (d == null) continue;
                    if (written >= MaxDoors) continue;

                    int did = IInt(delegate { return d.GetInstanceID(); }, 0);
                    bool open = BBool(delegate { return d.isOpen; }, false);
                    bool locked = BField(d, "isLocked", false);
                    bool canUnlock = BField(d, "canBeUnlocked", false);
                    int number = IInt(delegate { return d.doorNumber; }, -1);
                    bool preset = BBool(delegate { return d.isPresetZone; }, false);
                    int pz = IInt(delegate { return (int)d.presetZone; }, -1);

                    bool hasPos = false;
                    float px = 0f, py = 0f;
                    try
                    {
                        Transform tr = d.transform;
                        if (tr != null)
                        {
                            Vector3 p = tr.position;
                            px = p.x; py = p.y; hasPos = true;
                        }
                    }
                    catch { }

                    w.BeginObject();
                    w.Prop("uid", "o" + did);
                    w.Prop("open", open);
                    w.Prop("locked", locked);
                    w.Prop("can_unlock", canUnlock);
                    w.Prop("number", number);
                    w.Prop("preset_zone", preset);
                    w.Prop("preset_zone_id", pz);
                    w.Prop("preset_zone_name", Pick(ZoneNames, pz));
                    WriteVec(w, "pos", hasPos, px, py);
                    w.EndObject();

                    written++;
                }
            }
            w.EndArray();
            w.Prop("doors_total", total);
        }

        // ---------- EventButton：验证 / 证伪「地图节点 = EventButton」 ----------

        private static void WriteButtons(JsonWriter w)
        {
            UnityEngine.Object[] objs = null;
            try { objs = UnityEngine.Object.FindObjectsOfType(typeof(EventButton)); } catch { }

            int total = objs == null ? -1 : objs.Length;
            int written = 0;

            w.Name("event_buttons").BeginArray();
            if (objs != null)
            {
                for (int i = 0; i < objs.Length; i++)
                {
                    EventButton b = objs[i] as EventButton;
                    if (b == null) continue;
                    if (written >= MaxButtons) continue;

                    int bid = IInt(delegate { return b.GetInstanceID(); }, 0);
                    int req = IInt(delegate { return (int)b.requirement; }, -1);
                    int gold = IInt(delegate { return b.requiredGold; }, -1);
                    bool played = BBool(delegate { return b.played; }, false);
                    bool skippable = BBool(delegate { return b.skippable; }, false);
                    bool alwaysOn = BBool(delegate { return b.alwaysOn; }, false);
                    // buttonText 是 nonpublic，只能反射读
                    string text = "";
                    try
                    {
                        object raw = Field(b, "buttonText");
                        if (raw is string) text = (string)raw;
                    }
                    catch { }

                    DungeonEvent owner = null;
                    try { owner = Field(b, "dungeonEvent") as DungeonEvent; } catch { }

                    w.BeginObject();
                    w.Prop("uid", "b" + bid);
                    w.Prop("requirement", req);
                    w.Prop("required_gold", gold);
                    w.Prop("played", played);
                    w.Prop("skippable", skippable);
                    w.Prop("always_on", alwaysOn);
                    w.Prop("text", text);
                    WriteRef(w, "event", "d", owner);
                    w.EndObject();

                    written++;
                }
            }
            w.EndArray();
            w.Prop("event_buttons_total", total);
        }

        // ---------- 非战斗等待界面（战后奖励 / 升级） ----------

        private static void WriteUi(JsonWriter w, GameManager gm)
        {
            LevelUpManager lum = null;
            try { lum = LevelUpManager.main; } catch { }

            bool leveling = false;
            bool canLevel = false;
            if (lum != null)
            {
                leveling = BBool(delegate { return lum.levelingUp; }, false);
                canLevel = BBool(delegate { return lum.CanLevelUp(); }, false);
            }

            int victory = -1;
            int reward = -1;
            string selected = "";
            int curEvent = -1;
            try
            {
                if (gm != null)
                {
                    try { if (gm.victoryItems != null) victory = gm.victoryItems.Count; } catch { }
                    try { if (gm.rewardItems != null) reward = gm.rewardItems.Count; } catch { }
                    try
                    {
                        if (gm.selectedItem != null)
                            selected = SStr(delegate { return gm.selectedItem.displayName; });
                    }
                    catch { }
                    try { curEvent = (int)gm.currentEventType; } catch { }
                }
            }
            catch { }

            int dialogues = 0;
            bool dialogueFinished = false;
            try
            {
                UnityEngine.Object[] ds =
                    UnityEngine.Object.FindObjectsOfType(typeof(SelectItemDialogue));
                if (ds != null)
                {
                    dialogues = ds.Length;
                    if (ds.Length > 0) dialogueFinished = BField(ds[0], "finished", false);
                }
            }
            catch { }

            // 背包开着还是地图开着——mode 分不出来，这个字段能。
            int viewing = -1;
            int invPhase = -1;
            try { if (gm != null) viewing = (int)gm.viewingTop; } catch { }
            try { if (gm != null) invPhase = (int)gm.inventoryPhase; } catch { }

            // 教学关进度：教程是脚本化的，这个字段就是权威进度。
            TutorialManager tm = null;
            try { tm = TutorialManager.main; } catch { }
            int tutSeq = -1;
            int tutDone = -1;
            bool tutDisableLeveling = false;
            if (tm != null)
            {
                tutSeq = IInt(delegate { return (int)tm.tutorialSequence; }, -1);
                tutDisableLeveling = BBool(delegate { return tm.disableLevelingUp; }, false);
                try { if (tm.completedTutorials != null) tutDone = tm.completedTutorials.Count; } catch { }
            }

            // level / experience：一直读到 -1，先两个都输出，看是「游戏真给 -1」还是读失败。
            int level = -1;
            int exp = -1;
            try
            {
                Player pl = Player.main;
                if (pl != null)
                {
                    level = IInt(delegate { return pl.level; }, -1);
                    exp = IInt(delegate { return pl.experience; }, -1);
                }
            }
            catch { }

            // 升级 / 奖励的「可选内容」——之前只报了 levelingUp，agent 只能盲选。
            // 这几个字段让策略至少知道「有几个候选、已经选了几个」。
            int needUnlock = -1;
            int lvlOffered = -1;
            int lvlChosen = -1;
            try
            {
                if (lum != null)
                {
                    needUnlock = IInt(delegate { return lum.numberToUnlock; }, -1);
                    System.Collections.IList gl = World.Field(lum, "gridBlockPrefabs") as System.Collections.IList;
                    if (gl != null) lvlOffered = gl.Count;
                    System.Collections.IList cl = World.Field(lum, "chosenBlockPrefabs") as System.Collections.IList;
                    if (cl != null) lvlChosen = cl.Count;
                }
            }
            catch { }

            int dialogueChoices = -1;
            List<string> dialogueItems = new List<string>();
            try
            {
                UnityEngine.Object[] dlgObjs =
                    UnityEngine.Object.FindObjectsOfType(typeof(SelectItemDialogue));
                if (dlgObjs != null && dlgObjs.Length > 0)
                {
                    Transform ip = World.Field(dlgObjs[0], "itemParent") as Transform;
                    if (ip != null)
                    {
                        dialogueChoices = ip.childCount;
                        // 三选一 / 商店的候选名：以前只有个数，agent 只能固定拿第 0 个。
                        // 子物体是物品实例，优先读 Item2.displayName（"WOODEN SWORD"），
                        // 拿不到就退回 GameObject 名字（会带 "(Clone)"）。
                        for (int di = 0; di < ip.childCount && di < 16; di++)
                        {
                            Transform ch = ip.GetChild(di);
                            string nm = ch == null ? "" : ch.name;
                            Item2 chItem = null;
                            try { if (ch != null) chItem = ch.GetComponent<Item2>(); } catch { }
                            if (chItem == null)
                            {
                                try { if (ch != null) chItem = ch.GetComponentInChildren<Item2>(); } catch { }
                            }
                            if (chItem != null && !string.IsNullOrEmpty(chItem.displayName))
                                nm = chItem.displayName;
                            dialogueItems.Add(nm);
                        }
                    }
                }
            }
            catch { }

            w.Name("ui").BeginObject();
            w.Prop("viewing_top", viewing);
            w.Prop("viewing_top_name", Pick(ViewingTopNames, viewing));
            w.Prop("inventory_phase", invPhase);
            w.Prop("inventory_phase_name", Pick(InventoryPhaseNames, invPhase));
            bool inSpecialReorg = false;
            try { inSpecialReorg = gm.inSpecialReorg; } catch { }
            w.Prop("in_special_reorg", inSpecialReorg);
            bool skippableReorg = false;
            try { skippableReorg = gm.skippableReorganization; } catch { }
            w.Prop("skippable_reorganization", skippableReorg);
            w.Prop("player_level", level);
            w.Prop("player_experience", exp);
            w.Prop("popups", World.CountPopups());
            w.Prop("leveling_up", leveling);
            w.Prop("can_level_up", canLevel);
            w.Prop("victory_items", victory);
            w.Prop("reward_items", reward);
            w.Prop("selected_item", selected);
            w.Prop("current_event_type", curEvent);
            w.Prop("current_event_type_name", Pick(EventTypeNames, curEvent));
            w.Prop("select_item_dialogues", dialogues);
            w.Prop("select_item_finished", dialogueFinished);
            w.Prop("dialogue_choices", dialogueChoices);
            w.Name("dialogue_items").BeginArray();
            for (int di = 0; di < dialogueItems.Count; di++) w.Value(dialogueItems[di]);
            w.EndArray();
            w.Prop("number_to_unlock", needUnlock);
            w.Prop("levelup_offered", lvlOffered);
            w.Prop("levelup_chosen", lvlChosen);
            w.Prop("tutorial_sequence", tutSeq);
            w.Prop("tutorial_sequence_name", Pick(TutorialSeqNames, tutSeq));
            w.Prop("tutorial_completed", tutDone);
            w.Prop("tutorial_disable_leveling", tutDisableLeveling);
            w.EndObject();
        }

        // ---------- 写出小工具 ----------

        /// <summary>写 "uid" 一类的引用：null 或 "r123" / "d456"。</summary>
        private static void WriteRef(JsonWriter w, string name, string prefix, UnityEngine.Object obj)
        {
            if (obj == null)
            {
                w.Name(name).Null();
                return;
            }

            // 注意：GetInstanceID() 在这个游戏里经常是**负数**（物品 i-7936、房间 r-9056），
            // 所以不能用 id < 0 判失败——之前就是这么把 room_at / door 写成 null 的。
            int id = int.MinValue;
            try { id = obj.GetInstanceID(); } catch { id = int.MinValue; }

            if (id == int.MinValue) w.Name(name).Null();
            else w.Prop(name, prefix + id);
        }

        /// <summary>写 [x,y]；has=false 时写 null。</summary>
        private static void WriteVec(JsonWriter w, string name, bool has, float x, float y)
        {
            w.Name(name);
            if (!has)
            {
                w.Null();
                return;
            }
            w.BeginArray();
            w.Value(R1(x));
            w.Value(R1(y));
            w.EndArray();
        }
    }
}
