using MelonLoader;

[assembly: MelonInfo(typeof(BphBridge.BridgeMod), "BphBridge", "0.0.1", "ai-player-lab")]
// 注意：MelonGameAttribute 的签名是 (developer, name)，顺序不能反。
// 开发者在 MelonLoader 日志里是 "Game Developer: TheJaspel"，不是 "Jaspel"——
// 写成 Jaspel 会被判定为 "is incompatible: ... only compatible with 'Backpack Hero' by Jaspel"
// 然后静默变成 "0 Mods loaded"。
[assembly: MelonGame("TheJaspel", "Backpack Hero")]

namespace BphBridge
{
    public class BridgeMod : MelonMod
    {
        public override void OnInitializeMelon()
        {
            LoggerInstance.Msg("BphBridge loaded. game=" + UnityEngine.Application.productName);
        }

        public override void OnUpdate()
        {
            // 后面在这里轮询 runtime/cmd、写 state/ack。
        }
    }
}
