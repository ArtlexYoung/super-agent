import unittest

from super_agent.core.provider import MockModel
from super_agent.core.run import (
    RuntimeControl,
    RuntimeControlError,
    RunInput,
    collect_run,
    stream_run,
)


class RuntimeExtensionTests(unittest.TestCase):
    def test_extension_observes_one_model_call_without_changing_the_loop(self):
        observed = []

        class Extension:
            def before_model_call(self, context):
                observed.append(("before", context.turn, len(context.request.messages)))
                return None

            def after_model_call(self, context, result):
                observed.append(("after", context.turn, result["success"]))

        result = collect_run(
            stream_run(
                RunInput("hello", MockModel("answer"), runtime_extensions=(Extension(),))
            )
        )

        self.assertEqual("answer", result.text)
        self.assertEqual([("before", 1, 1), ("after", 1, True)], observed)

    def test_unsupported_stop_control_is_explicitly_rejected(self):
        class Extension:
            def before_model_call(self, _context):
                return RuntimeControl("stop", reason="not implemented yet")

            def after_model_call(self, _context, _result):
                return None

        with self.assertRaises(RuntimeControlError):
            collect_run(
                stream_run(
                    RunInput(
                        "hello", MockModel("answer"), runtime_extensions=(Extension(),)
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
