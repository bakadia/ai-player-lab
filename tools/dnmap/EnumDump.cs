// Dump every enum of an assembly as JSON: { "Type": { "Member": value } }.
//
// Why: the bridge stores enums as integers (Ildump/metadata only gives field order,
// not values). The item census needs a reusable int -> name table, so pull the
// **real constant values** from the metadata instead of assuming order.
//
//   EnumDump.exe "<Assembly-CSharp.dll>" > enums.json
//
// Read-only. ASCII only, C# 5.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using Mono.Cecil;

internal static class EnumDump
{
    private static int Main(string[] args)
    {
        if (args.Length < 1)
        {
            Console.WriteLine("usage: EnumDump <assembly>");
            return 2;
        }

        AssemblyDefinition asm = AssemblyDefinition.ReadAssembly(args[0]);
        StringBuilder sb = new StringBuilder();
        int count = 0;

        sb.Append("{\"assembly\":").Append(Quote(asm.Name.Name));
        sb.Append(",\"types\":{");
        foreach (TypeDefinition t in AllTypes(asm.MainModule))
        {
            if (!t.IsEnum) continue;
            if (t.Name == "<PrivateImplementationDetails>") continue;

            if (count > 0) sb.Append(',');
            // Cecil 用 '/' 表示嵌套类型，C# 反射用 '+'。统一成 '+'，和 types.json 对齐。
            sb.Append(Quote(t.FullName.Replace('/', '+'))).Append(":{");

            int n = 0;
            foreach (FieldDefinition f in t.Fields)
            {
                if (!f.IsStatic) continue;      // skip value__
                if (n > 0) sb.Append(',');
                int v = n;
                if (f.HasConstant && f.Constant != null)
                {
                    try { v = Convert.ToInt32(f.Constant, CultureInfo.InvariantCulture); }
                    catch { }
                }
                sb.Append(Quote(f.Name)).Append(':').Append(v.ToString(CultureInfo.InvariantCulture));
                n++;
            }
            sb.Append('}');
            count++;
        }
        sb.Append("},\"count\":").Append(count.ToString(CultureInfo.InvariantCulture)).Append('}');

        Console.WriteLine(sb.ToString());
        return 0;
    }

    private static string Quote(string s)
    {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.Length; i++)
        {
            char c = s[i];
            if (c == '"') b.Append("\\\"");
            else if (c == '\\') b.Append("\\\\");
            else if (c == '\n') b.Append("\\n");
            else if (c == '\r') b.Append("\\r");
            else if (c == '\t') b.Append("\\t");
            else if (c < ' ') b.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
            else b.Append(c);
        }
        return b.Append('"').ToString();
    }

    private static IEnumerable<TypeDefinition> AllTypes(ModuleDefinition mod)
    {
        Stack<TypeDefinition> stack = new Stack<TypeDefinition>();
        foreach (TypeDefinition t in mod.Types) stack.Push(t);
        while (stack.Count > 0)
        {
            TypeDefinition t = stack.Pop();
            yield return t;
            foreach (TypeDefinition n in t.NestedTypes) stack.Push(n);
        }
    }
}
