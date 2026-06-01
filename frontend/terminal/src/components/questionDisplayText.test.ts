import assert from 'node:assert/strict';
import {getQuestionDisplayText} from './questionDisplayText.js';

const choices = ['确认并提交', '修改（请说明）', '取消'];

assert.equal(
	getQuestionDisplayText(
		'确认 Commit 1 的 message 并执行?\n- 确认并提交\n- 修改（请说明）\n- 取消',
		choices,
	),
	'确认 Commit 1 的 message 并执行?',
);

assert.equal(
	getQuestionDisplayText('确认 Commit 1 的 message 并执行?', choices),
	'确认 Commit 1 的 message 并执行?',
);

assert.equal(
	getQuestionDisplayText('请选择要保留的说明:\n- 与选项无关', choices),
	'请选择要保留的说明:\n- 与选项无关',
);
