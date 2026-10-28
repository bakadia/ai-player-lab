// 只读的世界查询小工具。
//
// 为什么单独一个文件：`MapProbe`（写 state）和 `Snapshot`（判 changed）都要做
// 同样的查询，放一处避免两边漂移——最怕的就是「state 说在 A 房间、Snapshot 说没变」。

using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;

namespace BphBridge
{
    internal static class World
    {
        /// <summary>房间间距离阈值：格子是 1 单位一格，超过这个就认为「不在任何房间里」。</summary>
        public const float RoomSnapDistance = 0.6f;

        /// <summary>
        /// 离给定位置最近的房间。
        ///
        /// 不用游戏自带的 `DungeonRoom.GetRoom()`：实测刚切层时它对**坐标完全相等**的
        /// 房间也返回 null（SPIKE-06 §15）。自己按曼哈顿距离找，稳定。
        /// </summary>
        public static DungeonRoom NearestRoom(Vector3 pos, float maxDist, out float dist)
        {
            dist = float.MaxValue;
            DungeonRoom best = null;

            List<DungeonRoom> rooms = null;
            try { rooms = DungeonRoom.allRooms; } catch { }
            if (rooms == null) return null;

            for (int i = 0; i < rooms.Count; i++)
            {
                DungeonRoom r = rooms[i];
                if (r == null) continue;
                try
                {
                    Vector3 p = r.transform.position;
                    float d = Mathf.Abs(p.x - pos.x) + Mathf.Abs(p.y - pos.y);
                    if (d < dist) { dist = d; best = r; }
                }
                catch { }
            }

            if (best != null && dist > maxDist) return null;
            return best;
        }

        /// <summary>房间 uid，形如 "r123"；没有就返回空串。</summary>
        public static string RoomUid(DungeonRoom r)
        {
            if (r == null) return "";
            try { return "r" + r.GetInstanceID(); } catch { return ""; }
        }

        /// <summary>
        /// 玩家当前所在房间的 uid。
        /// 优先用 `DungeonPlayer.room`（到达房间后才有值），
        /// 没有就用最近房间兜底（刚切层、刚进游戏时就是这种）。
        /// </summary>
        public static string PlayerRoomUid()
        {
            try
            {
                DungeonPlayer dp = DungeonPlayer.main;
                if (dp == null) return "";
                if (dp.room != null) return RoomUid(dp.room);

                float dist;
                DungeonRoom r = NearestRoom(dp.transform.position, RoomSnapDistance, out dist);
                return r == null ? "" : RoomUid(r);
            }
            catch { return ""; }
        }

        /// <summary>场上的弹窗数量（TutorialManager / PopUpManager 用的是 BasicPopUp）。</summary>
        public static int CountPopups()
        {
            try
            {
                UnityEngine.Object[] objs =
                    UnityEngine.Object.FindObjectsOfType(typeof(BasicPopUp));
                return objs == null ? -1 : objs.Length;
            }
            catch { return -1; }
        }

        /// <summary>地牢事件总数。事件被清掉后会从列表里消失，所以它是「还剩几个」。</summary>
        public static int CountEvents()
        {
            try
            {
                List<DungeonEvent> es = DungeonEvent.dungeonEvents;
                if (es == null) return -1;
                int n = 0;
                for (int i = 0; i < es.Count; i++) if (es[i] != null) n++;
                return n;
            }
            catch { return -1; }
        }

        /// <summary>物品是否真的摆在格子里（有 gridPositions）。</summary>
        public static bool IsPlaced(Item2 it)
        {
            try
            {
                GridObject go = it.gridObject;
                if (go == null) return false;
                List<Vector2Int> pos = go.gridPositions;
                return pos != null && pos.Count > 0;
            }
            catch { return false; }
        }

        /// <summary>
        /// 真正在背包格子里面的物品。
        ///
        /// 注意：`IsPlaced`（有 gridPositions）**不等于**「在背包里」——地上的战利品
        /// 也有格子坐标。实测教程开局那 3 件是**摆在房间里**的，走开就被销毁了
        /// （游戏自己的 `DestroyAllItemsOutsideGrid`）。判断背包必须用这两个方法。
        /// </summary>
        public static List<Item2> ItemsInGrid()
        {
            try { return Item2.GetAllItemsInGrid(); } catch { return null; }
        }

        /// <summary>背包外的物品（地上战利品、奖励候选）。</summary>
        public static List<Item2> ItemsLoose()
        {
            try { return Item2.GetAllItemsOutsideGrid(); } catch { return null; }
        }

        public static int CountInGrid()
        {
            List<Item2> l = ItemsInGrid();
            return l == null ? -1 : l.Count;
        }

        public static int CountLoose()
        {
            List<Item2> l = ItemsLoose();
            return l == null ? -1 : l.Count;
        }

        /// <summary>
        /// 世界状态签名：地板 / 房间数 / 事件数 / 当前房间。
        /// 用来判断「换场景了没有」——出门、切层、进战斗都会让它变。
        /// </summary>
        public static string Signature()
        {
            int floor = -1;
            try
            {
                GameManager gm = GameManager.main;
                if (gm != null) floor = gm.floor;
            }
            catch { }

            int rooms = -1;
            try
            {
                List<DungeonRoom> rs = DungeonRoom.allRooms;
                if (rs != null) rooms = rs.Count;
            }
            catch { }

            return floor + "|" + rooms + "|" + CountEvents() + "|" + PlayerRoomUid();
        }

        /// <summary>
        /// 读 nonpublic 字段（逐层往基类找）。按钮、列表这类东西经常是 private。
        /// </summary>
        public static object Field(object obj, string name)
        {
            if (obj == null) return null;
            try
            {
                Type cur = obj.GetType();
                while (cur != null)
                {
                    FieldInfo fi = cur.GetField(name,
                        BindingFlags.Instance | BindingFlags.Public |
                        BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
                    if (fi != null) return fi.GetValue(obj);
                    cur = cur.BaseType;
                }
                return null;
            }
            catch { return null; }
        }

        /// <summary>
        /// 调 private/protected 方法（逐层往基类找）。
        /// 摆放这条链上关键的方法好几个是 private：`ItemMovement.EndDrag`、
        /// `GameManager.MoveItemTowardsGrid`。public 的入口不够用，只能反射。
        /// </summary>
        public static object CallPrivate(object obj, string name, object[] args)
        {
            if (obj == null) return null;
            try
            {
                Type t = obj.GetType();
                MethodInfo mi = null;
                Type cur = t;
                int argc = args == null ? 0 : args.Length;

                while (cur != null && mi == null)
                {
                    MethodInfo[] all = cur.GetMethods(
                        BindingFlags.Instance | BindingFlags.Public |
                        BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
                    for (int i = 0; i < all.Length; i++)
                    {
                        if (all[i].Name != name) continue;
                        if (all[i].GetParameters().Length != argc) continue;
                        mi = all[i];
                        break;
                    }
                    cur = cur.BaseType;
                }

                if (mi == null) return null;
                return mi.Invoke(obj, args);
            }
            catch { return null; }
        }

        /// <summary>写 private/protected/public 字段（逐层往基类找）。</summary>
        public static bool SetField(object obj, string name, object value)
        {
            if (obj == null) return false;
            try
            {
                Type cur = obj.GetType();
                while (cur != null)
                {
                    FieldInfo fi = cur.GetField(name,
                        BindingFlags.Instance | BindingFlags.Public |
                        BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
                    if (fi != null)
                    {
                        fi.SetValue(obj, value);
                        return true;
                    }
                    cur = cur.BaseType;
                }
                return false;
            }
            catch { return false; }
        }

        /// <summary>
        /// 调「参数是嵌套 enum」的 private/public 方法：调用方只知道整数值，
        /// 不引用那个 private 嵌套类型（跨程序集拿不到）。
        /// </summary>
        public static object CallPrivateEnum(object obj, string name, int enumValue)
        {
            if (obj == null) return null;
            try
            {
                Type cur = obj.GetType();
                while (cur != null)
                {
                    MethodInfo[] all = cur.GetMethods(
                        BindingFlags.Instance | BindingFlags.Public |
                        BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
                    for (int i = 0; i < all.Length; i++)
                    {
                        if (all[i].Name != name) continue;
                        ParameterInfo[] ps = all[i].GetParameters();
                        if (ps.Length != 1) continue;
                        object arg = enumValue;
                        if (ps[0].ParameterType.IsEnum)
                            arg = Enum.ToObject(ps[0].ParameterType, enumValue);
                        return all[i].Invoke(obj, new object[] { arg });
                    }
                    cur = cur.BaseType;
                }
                return null;
            }
            catch { return null; }
        }

        /// <summary>场上所有 Door（门/传送门）。没有静态列表，只能扫场景。</summary>
        public static List<Door> AllDoors()
        {
            try
            {
                UnityEngine.Object[] objs =
                    UnityEngine.Object.FindObjectsOfType(typeof(Door));
                if (objs == null) return null;
                List<Door> list = new List<Door>();
                for (int i = 0; i < objs.Length; i++)
                {
                    Door d = objs[i] as Door;
                    if (d != null) list.Add(d);
                }
                return list;
            }
            catch { return null; }
        }
    }
}
