/* Scores a submission from sourcerunner run statuses, producing a single
 * ACWA-style verdict like scorer_acwa.cpp.
 *
 * Usage: scorer_acwa_status <num_tests>
 *
 * For each test i in [1, num_tests] it reads two status inputs from the
 * working directory, each rendered by a sourcerunner `status` input:
 *
 *   sol.<i>  format "{state} {exit_type} {kill_reason}"  (submission run)
 *   chk.<i>  format "{state} {exit_type} {exit_status}"  (checker run)
 *
 * Missing values are rendered by sourcerunner as "-".
 *
 * Per-test verdicts:
 *   A  checker accepted
 *   W  checker rejected (testlib _wa/_pe) or the output file was never
 *      produced (checker skipped for a missing input)
 *   T  submission killed for exceeding the CPU or wall time limit
 *   R  submission exited non-zero, died to a signal, or ran out of memory
 *   C  submission run skipped (its build failed)
 *   E  anything else (internal errors, checker failures, unreadable status)
 *
 * The final verdict, written to stdout, is the first non-A per-test verdict in
 * test order, or A if every test was accepted. Per-test verdicts are written
 * to stderr.
 */
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>

using namespace std;

static bool read_status(const string& path, string& state, string& exit_type,
                        string& extra) {
  ifstream fin(path.c_str());
  return static_cast<bool>(fin >> state >> exit_type >> extra);
}

static char test_verdict(int test) {
  string idx = to_string(test);

  string sol_state, sol_exit_type, sol_kill_reason;
  if (!read_status("sol." + idx, sol_state, sol_exit_type, sol_kill_reason)) {
    return 'E';
  }
  if (sol_state == "skipped") {
    return 'C';
  }
  if (sol_state == "failed") {
    if (sol_exit_type == "killed") {
      if (sol_kill_reason == "TIMEOUT_CPU" ||
          sol_kill_reason == "TIMEOUT_WALL") {
        return 'T';
      }
      return 'E';
    }
    if (sol_exit_type == "normal" || sol_exit_type == "signal" ||
        sol_exit_type == "oom") {
      return 'R';
    }
    return 'E';
  }
  if (sol_state != "success") {
    return 'E';
  }

  string chk_state, chk_exit_type, chk_exit_status;
  if (!read_status("chk." + idx, chk_state, chk_exit_type, chk_exit_status)) {
    return 'E';
  }
  if (chk_state == "success") {
    return 'A';
  }
  if (chk_state == "skipped") {
    return 'W';
  }
  if (chk_state == "failed" && chk_exit_type == "normal" &&
      (chk_exit_status == "1" || chk_exit_status == "2")) {
    return 'W';
  }
  return 'E';
}

int main(int argc, char** argv) {
  if (argc < 2) {
    cout << "E" << endl;
    return 0;
  }

  int tests = atoi(argv[1]);
  char result = 'A';
  for (int i = 1; i <= tests; i++) {
    char verdict = test_verdict(i);
    cerr << i << " " << verdict << endl;
    if (result == 'A') {
      result = verdict;
    }
  }
  if (tests <= 0) {
    result = 'E';
  }

  cout << result << endl;
  return 0;
}
