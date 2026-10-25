// runtime/ 目录解析。
//
// 桥接进程在游戏里，Python agent 在仓库里，两边都得找到同一个目录。
// 默认指向仓库里的 runtime/（.gitignore 已覆盖 games/*/runtime/）；
// 想换位置就在 Mods 旁边放一个 BphBridge.runtime.txt，里面写一行绝对路径。

using System;
using System.IO;
using MelonLoader;

namespace BphBridge
{
    internal static class RuntimePaths
    {
        public const string DefaultDir = @"D:\projects\ai-player-lab\games\backpack-hero-aipeiwo\runtime";

        public static string Dir = DefaultDir;
        public static string StateFile = Path.Combine(DefaultDir, "state.json");
        public static string CmdDir = Path.Combine(DefaultDir, "cmd");
        public static string AckDir = Path.Combine(DefaultDir, "ack");
        public static string HaltFile = Path.Combine(DefaultDir, "halt");

        public static void Init(string assemblyLocation)
        {
            try
            {
                string dllDir = Path.GetDirectoryName(assemblyLocation);
                if (!string.IsNullOrEmpty(dllDir))
                {
                    string cfg = Path.Combine(dllDir, "BphBridge.runtime.txt");
                    if (File.Exists(cfg))
                    {
                        string line = File.ReadAllText(cfg).Trim();
                        if (line.Length > 0)
                        {
                            Dir = line;
                            MelonLogger.Msg("[BphBridge] runtime dir overridden: " + Dir);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                MelonLogger.Warning("[BphBridge] runtime cfg failed, using default: " + ex.Message);
            }

            StateFile = Path.Combine(Dir, "state.json");
            CmdDir = Path.Combine(Dir, "cmd");
            AckDir = Path.Combine(Dir, "ack");
            HaltFile = Path.Combine(Dir, "halt");

            try
            {
                Directory.CreateDirectory(Dir);
                Directory.CreateDirectory(CmdDir);
                Directory.CreateDirectory(AckDir);
            }
            catch (Exception ex)
            {
                MelonLogger.Error("[BphBridge] cannot create runtime dirs: " + ex.Message);
            }
        }
    }
}
