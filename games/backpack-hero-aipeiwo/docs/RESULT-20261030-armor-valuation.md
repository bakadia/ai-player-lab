# RESULT · 2026-10-30 · 护甲流定价：每回合 7 甲 = 1 费

> 用户口径（2026-10-30）：「不用留一格吧，可以替换一件装备……核心就是一套流派 +
> 几个辅助物品 + 若干消耗品。防具帽子手套鞋子衣服，每回合自动给护甲，
> 基本可以看作 7 护甲 = 1 费，收益还挺高。」
>
> 这一轮的活儿就是把这个口径变成代码里的价格，替换掉原来明显偏低的估值；
> 顺带挖出一个把 -999 哨兵算成 999 加成的脏数据 bug。

## 1. 改之前错在哪

`item_value()`（决定「该丢哪件 / 该留哪件 / 值不值得腾位」）对每回合护甲只给：

```python
value += 2.0 + st.block_per_turn * 0.5      # Knight's Armor 6/turn → 5.0
```

而一把木剑在账本里是 **5.4**。也就是说**护甲比木剑还低**，腾位时第一件被丢的
正是流派核心——真机日志里也确实是这个顺序：

```
腾位：丢 SLATS SHIELD（2 格，剩余 0 次）
腾位：丢 MANE STONE（1 格，剩余 0 次）
```

`reward_score()`（N 选 1）更轻：`st.block_per_turn * 1.5`（6/turn → 9 分），
而一把 1 费 7 伤的剑是 14 分。

## 2. 现在的价格

`agent/policy.py` 里三个常量 + 两个纯函数，**一个口径、两处复用**：

```python
BLOCK_PER_ENERGY = 7.0   # 7 护甲 ≈ 1 费（ROUGH BUCKLER：1 费 / 7 挡，实测基准）
FIGHT_TURNS      = 3.0   # 一场战斗按 3 回合折现（M4 实测 30 end_turn / 8 场 ≈ 3.75，取保守）
ENERGY_SCORE     = 14.0  # 1 费在 reward_score 里值多少（1 费 7 伤 = 7 × 2.0）
DAMAGE_PER_ENERGY = 5.4  # 1 费在「等价伤害」里值多少（账本木剑实测）

def per_turn_energy(bpt):  return bpt / 7 * 3        # 等效能量
def per_turn_value(bpt):   return per_turn_energy(bpt) * 5.4   # 等价伤害
```

- `7 甲/回合` = **3 费** = 16.2 等价伤害；
- `Knight's Armor`（6/turn、4 格）= 2.57 费 = **14.9**（木剑 5.4）；
- `Plate Armor`（8/turn）= **19.5**，全库最高；
- `Bird Chant`（6/turn 只要 **1 格**）= 14.9 —— 一格换 2.57 费，这就是「收益还挺高」。

用在两处：

| 位置 | 改动 |
|---|---|
| `policy.reward_score`（N 选 1 / 商店） | `block_active*0.5 + per_turn_energy(bpt)*ENERGY_SCORE` |
| `backpack.item_value`（丢谁 / 留谁 / 值不值得换） | `1.0 + per_turn_value(bpt)`；顺手补了「没试过的一次性格挡」给 `0.5 + block*0.5`（原来只有 0.5，等于永远第一个被丢） |

## 3. 顺带修掉的脏数据（E26）

给全库排序时发现 5 件道具的 `item_value` 是 **300.70**：

```
300.70  Ballista Variant / Bashing Shield / Candy Pail / Ghost Gem / Venom Sword
```

根因在 `itemdb.py` 的「给别的道具加成」：

```python
support_damage += abs(value)          # ← 没有哨兵保护
```

prefab 上 `-999` 是哨兵（PITFALLS E20），`abs(-999)` 就成了 **+999**，
乘 0.3 直接顶到 300 分——这 5 件在腾位排序里永远不会被丢，也会污染 N 选 1。

现在 `|value| >= 900` 一律当 0，并对单件加成上钳到 50（`SENTINEL` / `MAX_SUPPORT`）。
修完全库最高值 19.51（Plate Armor），分布恢复正常。

## 4. 验证

- 单测：`test_backpack.py::test_armor_priced_above_sword`
  （`per_turn_energy(7)==3`、护甲值 > 木剑 ×1.5、丢的顺序是「杂物 → 剑 → 甲」），
  `test_v0.py::test_reward_pick` 里补了「N 选 1 时 6 甲/回合 压过 1 费 7 伤」。
  四套测试全 ALL PASS。
- 真机 3 局（`data/runs/armor/`，floor 2 / 2 / 1，全阵亡）：
  - 护甲成了硬保护：`放弃 MACHETE：要丢的 ROUGH BUCKLER 值 7.00，比它自己 6.00 还高`、
    `放弃 MEAL：要丢的 ROUGH BUCKLER 值 7.00`——盾牌不再被拿去换武器。
  - **但这 3 局没出现过「每回合给甲」的防具**（只有一件 `FLOATY 1/turn` 被拿进来），
    所以「护甲流真的更强」这一点**还没有真机证据**，只能说估值改正了、行为没再反着来。

## 5. 没做的

- **「留一格空格」的方案取消**（用户判断：不需要，替换一件装备就行）。
  腾位仍然走 `place.py` 的 drop/swap，只是候选排序现在按新价格算。
- **流派识别**（「一套流派 + 几个辅助 + 若干消耗品」）没做：现在仍然按单件价值排，
  没有「已经 3 件护甲了，第 4 件边际递减」这种组合判断。
- 每回合护甲也**没有进战斗打分**（它是被动、不出现在 `legal` 里），
  只影响「拿/留/丢」——这和它实际生效的方式一致。

## 6. 下一轮

1. **拿真机 A/B 验证护甲流**：现在估值对了，可以对比「优先拿每回合护甲」vs「优先拿输出」，
   看每场掉血和存活层数——但要先解决「遇不到护甲」的样本问题（3 局只出现 1 件）。
2. **边际递减**：第 2/3 件同类护甲的收益会掉（`min(block, incoming)` 会被浪费），
   `per_turn_energy` 可以按「当前已在包里的每回合甲」折价。
3. `FIGHT_TURNS = 3` 是拍的（M4 均值），跑深一点（floor 3-4）之后应该重算。
