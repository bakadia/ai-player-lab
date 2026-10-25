// dnmap — dump a .NET (Mono/Unity) assembly's type map to JSON.
//
// 用途：把 Assembly-CSharp.dll 的类型/字段/方法/属性导成可 grep 的索引，
// 用来定位 GameManager / Player / Enemy / 背包 / 回合状态，写状态读取。
// 只读元数据，不执行任何游戏代码，不依赖 .NET SDK（用系统自带 csc + Mono.Cecil）。
//
// 编译：tools\dnmap\build.ps1
// 用法：Dump.exe <assembly> <outDir>

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using Mono.Cecil;

internal static class Dump
{
    private static string Esc(string s)
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
            else if (c < ' ')
                b.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
            else b.Append(c);
        }
        return b.Append('"').ToString();
    }

    private static string Bool(bool v)
    {
        return v ? "true" : "false";
    }

    // Cecil 的 FullName 已经是「命名空间.类型<泛型实参>」，够用；
    // 嵌套类型用 '/'，换成 '+' 更像 C# 写法。
    private static string TypeName(TypeReference t)
    {
        if (t == null) return null;
        try
        {
            return t.FullName.Replace('/', '+');
        }
        catch
        {
            return t.Name;
        }
    }

    private static string SafeBase(TypeDefinition td)
    {
        try
        {
            return TypeName(td.BaseType);
        }
        catch
        {
            return "?";
        }
    }

    private static void Walk(TypeDefinition td, List<TypeDefinition> acc)
    {
        acc.Add(td);
        foreach (TypeDefinition nested in td.NestedTypes) Walk(nested, acc);
    }

    private static string TypeJson(TypeDefinition td)
    {
        StringBuilder sb = new StringBuilder();
        sb.Append("{\"full\":").Append(Esc(TypeName(td)));
        sb.Append(",\"name\":").Append(Esc(td.Name));
        sb.Append(",\"namespace\":").Append(Esc(td.Namespace));
        sb.Append(",\"base\":").Append(Esc(SafeBase(td)));
        sb.Append(",\"enum\":").Append(Bool(td.IsEnum));
        sb.Append(",\"interface\":").Append(Bool(td.IsInterface));
        sb.Append(",\"abstract\":").Append(Bool(td.IsAbstract));
        sb.Append(",\"sealed\":").Append(Bool(td.IsSealed));
        sb.Append(",\"public\":").Append(Bool(td.IsPublic || td.IsNestedPublic));

        sb.Append(",\"fields\":[");
        bool first = true;
        foreach (FieldDefinition fd in td.Fields)
        {
            if (!first) sb.Append(',');
            first = false;
            sb.Append("{\"name\":").Append(Esc(fd.Name));
            sb.Append(",\"type\":").Append(Esc(TypeName(fd.FieldType)));
            sb.Append(",\"static\":").Append(Bool(fd.IsStatic));
            sb.Append(",\"public\":").Append(Bool(fd.IsPublic));
            if (fd.HasConstant)
            {
                string c;
                try
                {
                    c = Convert.ToString(fd.Constant, CultureInfo.InvariantCulture);
                }
                catch
                {
                    c = null;
                }
                sb.Append(",\"const\":").Append(Esc(c));
            }
            sb.Append('}');
        }
        sb.Append(']');

        sb.Append(",\"methods\":[");
        first = true;
        foreach (MethodDefinition md in td.Methods)
        {
            if (!first) sb.Append(',');
            first = false;
            sb.Append("{\"name\":").Append(Esc(md.Name));
            sb.Append(",\"ret\":").Append(Esc(TypeName(md.ReturnType)));
            sb.Append(",\"static\":").Append(Bool(md.IsStatic));
            sb.Append(",\"public\":").Append(Bool(md.IsPublic));
            sb.Append(",\"virtual\":").Append(Bool(md.IsVirtual));
            sb.Append(",\"abstract\":").Append(Bool(md.IsAbstract));
            sb.Append(",\"ctor\":").Append(Bool(md.IsConstructor));
            sb.Append(",\"params\":[");
            bool pfirst = true;
            foreach (ParameterDefinition p in md.Parameters)
            {
                if (!pfirst) sb.Append(',');
                pfirst = false;
                sb.Append("{\"name\":").Append(Esc(p.Name));
                sb.Append(",\"type\":").Append(Esc(TypeName(p.ParameterType)));
                sb.Append('}');
            }
            sb.Append("]}");
        }
        sb.Append(']');

        sb.Append(",\"properties\":[");
        first = true;
        foreach (PropertyDefinition pd in td.Properties)
        {
            if (!first) sb.Append(',');
            first = false;
            sb.Append("{\"name\":").Append(Esc(pd.Name));
            sb.Append(",\"type\":").Append(Esc(TypeName(pd.PropertyType)));
            sb.Append(",\"get\":").Append(Bool(pd.GetMethod != null));
            sb.Append(",\"set\":").Append(Bool(pd.SetMethod != null));
            sb.Append('}');
        }
        sb.Append("]}");
        return sb.ToString();
    }

    public static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: Dump.exe <assembly> <outDir>");
            return 2;
        }

        string asmPath = Path.GetFullPath(args[0]);
        string outDir = Path.GetFullPath(args[1]);
        if (!File.Exists(asmPath))
        {
            Console.Error.WriteLine("assembly not found: " + asmPath);
            return 1;
        }
        Directory.CreateDirectory(outDir);

        DefaultAssemblyResolver resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(Path.GetDirectoryName(asmPath));

        ReaderParameters rp = new ReaderParameters();
        rp.AssemblyResolver = resolver;
        rp.InMemory = true;
        rp.ReadSymbols = false;

        AssemblyDefinition asm;
        try
        {
            asm = AssemblyDefinition.ReadAssembly(asmPath, rp);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("failed to read assembly: " + ex.Message);
            return 1;
        }

        List<TypeDefinition> types = new List<TypeDefinition>();
        foreach (TypeDefinition t in asm.MainModule.Types) Walk(t, types);

        StringBuilder json = new StringBuilder();
        json.Append("{\"assembly\":").Append(Esc(asm.Name.Name));
        json.Append(",\"version\":").Append(Esc(asm.Name.Version.ToString()));
        json.Append(",\"typeCount\":").Append(types.Count);
        json.Append(",\"types\":[");

        StringBuilder index = new StringBuilder();
        index.Append("# ").Append(asm.Name.Name).Append(' ').Append(asm.Name.Version.ToString()).Append('\n');
        index.Append("# typeCount=").Append(types.Count).Append('\n');
        index.Append("# COLS: full<TAB>base<TAB>fields<TAB>methods<TAB>props<TAB>flags\n");

        int failed = 0;
        using (StreamWriter jw = new StreamWriter(Path.Combine(outDir, "types.json"), false, new UTF8Encoding(false)))
        {
            jw.Write(json.ToString());
            json = null;

            bool first = true;
            foreach (TypeDefinition td in types)
            {
                string name = "?";
                try
                {
                    name = TypeName(td);
                    if (!first) jw.Write(',');
                    jw.Write('\n');
                    jw.Write(TypeJson(td));
                    first = false;

                    List<string> flags = new List<string>();
                    if (td.IsEnum) flags.Add("enum");
                    if (td.IsInterface) flags.Add("interface");
                    if (td.IsAbstract) flags.Add("abstract");
                    if (td.IsSealed) flags.Add("sealed");
                    if (td.IsPublic || td.IsNestedPublic) flags.Add("public");

                    index.Append(name).Append('\t');
                    index.Append(SafeBase(td)).Append('\t');
                    index.Append(td.Fields.Count.ToString(CultureInfo.InvariantCulture)).Append('\t');
                    index.Append(td.Methods.Count.ToString(CultureInfo.InvariantCulture)).Append('\t');
                    index.Append(td.Properties.Count.ToString(CultureInfo.InvariantCulture)).Append('\t');
                    index.Append(string.Join(",", flags.ToArray())).Append('\n');
                }
                catch (Exception ex)
                {
                    failed++;
                    jw.Write('\n');
                    jw.Write("{\"full\":");
                    jw.Write(Esc(name));
                    jw.Write(",\"error\":");
                    jw.Write(Esc(ex.Message));
                    jw.Write('}');
                }
            }
            jw.Write("\n]}\n");
        }

        File.WriteAllText(Path.Combine(outDir, "index.tsv"), index.ToString(), new UTF8Encoding(false));

        Console.WriteLine("assembly : " + asm.Name.Name + " " + asm.Name.Version.ToString());
        Console.WriteLine("types    : " + types.Count + " (failed " + failed + ")");
        Console.WriteLine("out      : " + Path.Combine(outDir, "types.json"));
        return 0;
    }
}
