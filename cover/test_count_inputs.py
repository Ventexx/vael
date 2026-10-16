import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import app


def fixture():
    wf = {'count': {'class_type': 'PrimitiveInt', 'inputs': {'value': 4},
                    '_meta': {'title': 'COUNT 2-5'}}}
    for i in range(1, 6):
        wf[str(i)] = {'class_type': 'LoadImage', 'inputs': {'image': 'old.png'},
                      '_meta': {'title': str(i)}}
    previous = ['1', 0]
    for i in range(2, 6):
        wf[f'join{i}'] = {'class_type': 'ImageStitch', 'inputs': {
            'image1': previous, 'image2': [str(i), 0]}}
        previous = [f'join{i}', 0]
    for i in (4, 3, 2):
        wf[f'eq{i}'] = {'class_type': 'ComfyMathExpression', 'inputs': {
            'expression': f'a == {i}', 'values.a': ['count', 0]}}
        wf[f'switch{i}'] = {'class_type': 'ComfySwitchNode', 'inputs': {
            'switch': [f'eq{i}', 2], 'on_true': [f'join{i}', 0], 'on_false': previous}}
        previous = [f'switch{i}', 0]
    wf['save'] = {'class_type': 'SaveImage', 'inputs': {'images': previous}}
    return wf


class CountInputsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = app.QApplication.instance() or app.QApplication([])

    def test_all_counts_and_original_preserved(self):
        original = fixture()
        snapshot = copy.deepcopy(original)
        for count in range(2, 6):
            wf = app.prepare_workflow(original, 'count', {'value': count})
            self.assertEqual(set(app.find_load_image_nodes(wf)), {str(i) for i in range(1, count+1)})
            self.assertEqual(wf['save']['inputs']['images'], [f'join{count}', 0])
            for node in wf.values():
                for value in node['inputs'].values():
                    if isinstance(value, list):
                        self.assertIn(value[0], wf)
        self.assertEqual(original, snapshot)

    def test_unrelated_workflow_unchanged(self):
        wf = fixture()
        wf['count']['_meta']['title'] = 'Some other value'
        self.assertEqual(app.prepare_workflow(wf, 'count', {}), wf)

    def test_unknown_condition_keeps_inputs(self):
        wf = fixture()
        wf['eq2']['inputs']['expression'] = 'unknown_function(a)'
        prepared = app.prepare_workflow(wf, 'count', {'value': 2})
        self.assertEqual(len(app.find_load_image_nodes(prepared)), 5)

    def test_invalid_count_rejected(self):
        for count in (1, 6, 2.5):
            with self.assertRaises(ValueError):
                app.prepare_workflow(fixture(), 'count', {'value': count})

    def test_submission_uploads_only_active_inputs(self):
        with patch.object(app, 'ComfyAPI') as api:
            client = api.return_value
            client.upload_image.return_value = ('uploaded.png', '', 'input')
            client.queue_prompt.side_effect = RuntimeError('stop after submission')
            with self.assertRaisesRegex(RuntimeError, 'stop after submission'):
                app.execute_workflow_sync('unused', fixture(),
                    {'1': 'one.png', '2': 'two.png', '3': None, '4': None, '5': None},
                    'count', {'value': 2})
            self.assertEqual(client.upload_image.call_count, 2)
            sent = client.queue_prompt.call_args.args[0]
            self.assertEqual(set(app.find_load_image_nodes(sent)), {'1', '2'})

    def test_roster_reorder_count_and_interactivity(self):
        class Window(app.QWidget):
            def persist_all(self): pass
        window = Window()
        state = app.WorkflowState(window)
        state.raw_workflow = fixture()
        state.optional_node_id = 'count'
        state.param_values = {'value': 3}
        state.slots = [{'node_id': str(i), 'caption': str(i),
                        'filepath': 'remembered.png' if i == 5 else None}
                       for i in (2, 3, 5, 4, 1)]
        with patch.object(app, '_read_preview', return_value=app.QImage()):
            bar = app.RosterBar(window)
            bar.set_workflow(state)
            self.assertEqual([icon.used_for_run for icon in bar.icons], [True, True, False, False, True])
            faded = bar.icons[2]
            self.assertTrue(faded.isEnabled())
            self.assertTrue(faded.acceptDrops())
            bar._on_arm_toggled(2)
            self.assertEqual(bar.armed_index, 2)
            bar.param_widgets['value'].setValue(5)
            self.assertTrue(all(icon.used_for_run for icon in bar.icons))
            self.assertEqual(state.slots[2]['filepath'], 'remembered.png')
            bar.param_widgets['value'].setValue(2)
            self.assertFalse(faded.used_for_run)
            faded.clear_image()
            self.assertIsNone(state.slots[2]['filepath'])
            for slot in state.slots:
                if slot['node_id'] in {'1', '2'}:
                    slot['filepath'] = 'present.png'
            self.assertTrue(state._validate())
            bar.close()
        window.close()

    def test_queue_preserves_count_and_images_at_enqueue_time(self):
        items = []
        window = SimpleNamespace(queue_manager=SimpleNamespace(add_item=items.append), server='unused')
        state = app.WorkflowState(window)
        state.raw_workflow = fixture()
        state.optional_node_id = 'count'
        state.param_values = {'value': 2}
        state.slots = [{'node_id': str(i), 'caption': str(i),
                        'filepath': f'{i}.png' if i <= 2 else None} for i in range(1, 6)]
        state.add_to_queue()
        state.param_values['value'] = 5
        state.slots[0]['filepath'] = 'changed.png'
        queued = items[0]
        self.assertEqual(queued['image_map']['1'], '1.png')
        prepared = app.prepare_workflow(queued['raw_workflow'], queued['optional_node_id'], queued['param_values'])
        self.assertEqual(set(app.find_load_image_nodes(prepared)), {'1', '2'})


if __name__ == '__main__':
    unittest.main()
