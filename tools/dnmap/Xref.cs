// 找「谁读/谁写了这个字段 / 谁调了这个方法」—— 静态交叉引用。
//
// 为什么需要：Ildump 能看单个方法干了什么，但回答不了「spacesSaved 到底在哪儿被赋值」
// 这种「反查」问题；靠一个个 dump 猜太慢。这个工具把整个程序集扫一遍，把命中点按
// 方法聚合并标出是读还是写。
//
//   Xref.exe <assembly> <Type> <Member>
//
//   Type     类型短名（LevelUpManager）或全名（BackpackHero.LevelUpManager）
//   Member   字段名 / 方法名 / 属性名
//
// 输出（纯 ASCII，GBK 控制台安全）：
//
//   == LevelUpManager::EndLevelUp  [w1 r0]
//      W  IL_00de  stfld  spacesSaved
//   -- 1 writer(s), 3 reader(s), 0 call(s)
//
// 只读：不执行任何游戏代码。
using System;
using System.Collections.Generic;
using Mono.Cecil;
using Mono.Cecil.Cil;

internal static class Xref
{
    private sealed class Hit
    {
        public string Kind = "";      // W / R / C
        public int Offset;
        public string Op = "";
        public string Target = "";
    }

    private static int Main(string[] args)
    {
        if (args.Length < 3)
        {
            Console.WriteLine("usage: Xref <assembly> <Type> <Member>");
            return 2;
        }

        AssemblyDefinition asm = AssemblyDefinition.ReadAssembly(args[0]);
        string wantType = args[1];
        string wantMember = args[2];

        int writers = 0, readers = 0, calls = 0, methods = 0;

        foreach (TypeDefinition t in AllTypes(asm.MainModule))
        {
            foreach (MethodDefinition m in t.Methods)
            {
                if (!m.HasBody) continue;
                List<Hit> hits = new List<Hit>();
                foreach (Instruction ins in m.Body.Instructions)
                {
                    string kind = null;
                    string target = null;

                    FieldReference fr = ins.Operand as FieldReference;
                    if (fr != null && fr.Name == wantMember && TypeMatches(fr.DeclaringType, wantType))
                    {
                        target = Simple(fr.DeclaringType) + "::" + fr.Name;
                        if (ins.OpCode.Code == Code.Stfld || ins.OpCode.Code == Code.Stsfld)
                            kind = "W";
                        else
                            kind = "R";
                    }

                    MethodReference mr = ins.Operand as MethodReference;
                    if (mr != null && mr.Name == wantMember && TypeMatches(mr.DeclaringType, wantType))
                    {
                        kind = "C";
                        target = Simple(mr.DeclaringType) + "::" + mr.Name;
                    }

                    if (kind == null) continue;
                    Hit h = new Hit();
                    h.Kind = kind;
                    h.Offset = ins.Offset;
                    h.Op = ins.OpCode.Name;
                    h.Target = target;
                    hits.Add(h);
                }

                if (hits.Count == 0) continue;

                methods++;
                int w = 0, r = 0, c = 0;
                foreach (Hit h in hits)
                {
                    if (h.Kind == "W") w++;
                    else if (h.Kind == "R") r++;
                    else c++;
                }
                writers += w; readers += r; calls += c;

                Console.WriteLine("== " + t.FullName + "::" + m.Name
                    + "  [w" + w + " r" + r + " c" + c + "]");
                foreach (Hit h in hits)
                {
                    Console.WriteLine("   " + h.Kind + "  IL_" +
                        h.Offset.ToString("x4") + "  " + h.Op + "  " + h.Target);
                }
            }
        }

        Console.WriteLine("-- " + methods + " method(s): " + writers + " write(s), "
            + readers + " read(s), " + calls + " call(s)");
        return methods == 0 ? 3 : 0;
    }

    private static bool TypeMatches(TypeReference tr, string want)
    {
        if (tr == null) return false;
        if (tr.Name == want) return true;
        return tr.FullName == want;
    }

    private static string Simple(TypeReference tr)
    {
        return tr == null ? "?" : tr.Name;
    }

    private static IEnumerable<TypeDefinition> AllTypes(ModuleDefinition mod)
    {
        foreach (TypeDefinition t in mod.Types)
        {
            yield return t;
            foreach (TypeDefinition n in Nested(t)) yield return n;
        }
    }

    private static IEnumerable<TypeDefinition> Nested(TypeDefinition t)
    {
        foreach (TypeDefinition n in t.NestedTypes)
        {
            yield return n;
            foreach (TypeDefinition d in Nested(n)) yield return d;
        }
    }
}
