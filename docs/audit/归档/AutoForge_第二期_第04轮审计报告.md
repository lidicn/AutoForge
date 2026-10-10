AutoForge 第二期第四轮审计报告
•
审计目标：AutoForge（lidicn/AutoForge）
•
轮次：round-004
•
上一轮：round-003（确证 AF5，证伪 CONC-08 ×5）
一句话结论
RSC-05 三条（_walk / _nnf）原仓库确为裸递归无预算，但端到端不可达——被更上游的 schema 校验崩溃（AF1，150 层）掩盖。这是"一个缺陷掩盖另一个缺陷"，安全性来自执行顺序这一偶然，不是设计。已加纵深防御预算。另发现判据缺口 W139：规则只看函数体，看不见装饰器提供的环检测与深度预算。
一、确证缺陷
AF7 · _walk / _walk_operand 裸递归无预算（low）
位置：af_ir/expr.py:255、af_ir/expr.py:270
能力确认（直接喂深嵌套，绕过上游）：
depth
	
原仓库
	
补丁副本


100
	
ok