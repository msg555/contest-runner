/* Does some post processing stuff for the each test case run for the ICPCGate
 * xjudge implementation.  Its duties include:
 *
 * 1) Determining the verdict for the test case.  This is useful so the
 *    submission may be short circuited and avoids sending the grader output
 *    back.
 * 2) Truncating any passed files to the first 4K bytes.  This is useful for
 *    returning, e.g., program stderr output while avoiding transmitting/storing
 *    large files.
 */
#include <iostream>
#include <string>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

using namespace std;

#define TRUNCATE_SIZE 4096

char buf[TRUNCATE_SIZE];

string oktokens[] = {"A", "AC", "CORRECT", "OK",
                     "OK!", "YES", "MOO", "TAK", ""};

void signal_ok() {
  fclose(fopen("_pp_ok", "w"));
}

int post_process_truncate(int argc, char** argv) {
  /* Truncate any input files. */
  for (int i = 1; i < argc; i++) {
    FILE* f = fopen(argv[i], "r");
    if (!f) {
      continue;
    }

    size_t amt = fread(buf, 1, TRUNCATE_SIZE, f);
    fclose(f);

    string opath = string(argv[i]) + ".out";
    f = fopen(opath.c_str(), "w");
    if (!f) {
      continue;
    }

    fwrite(buf, 1, amt, f);
    fclose(f);
  }
  return 0;
}

int post_process_truncate_ext(int argc, char** argv) {
  /* Truncate any input files. */
  for (int i = 1; i + 1 < argc; ) {
    int num_files = atoi(argv[i]);
    string opath = argv[i + 1];
    if (i + 2 + num_files > argc) {
      return 1;
    }

    FILE* f = fopen(opath.c_str(), "w");
    for (int j = 0; j < num_files; j++) {
      FILE* fin = fopen(argv[i + 2 + j], "r");
      if (!fin) {
        continue;
      }

      size_t amt = fread(buf, 1, TRUNCATE_SIZE, fin);
      fclose(fin);

      fwrite(buf, 1, amt, f);
    }
    i += 2 + num_files;

    fclose(f);
  }
  return 0;
}

int post_process_sub(int argc, char** argv) {
  if (argc <= 4) {
    /* Do nothing if given few arguments. */
    return 0;
  }

  /* Determine the verdict. */
  bool is_num = false;
  string verdict;
  string exitstatus_sub = argv[1];
  string exitstatus_grad = argv[2];
  int exitcode_grad = atoi(argv[3]);
  if (exitstatus_sub == "error" || exitstatus_grad == "error") {
    verdict = "E";
  } else if (exitstatus_sub == "tle") {
    verdict = "T";
  } else if (exitstatus_sub == "rte") {
    verdict = "R";
  } else if (exitstatus_sub != "ok") {
    verdict = "E";
  } else if (exitstatus_grad == "skipped") {
    verdict = "W"; /* Did not produce output file. */
  } else if (exitstatus_grad != "ok") {
    verdict = "E";
  } else if (exitcode_grad != 0) {
    verdict = "W";
  } else {
    string tok;
    if (cin >> tok) {
      for (int i = 0; i < tok.size(); i++) {
        tok[i] = toupper(tok[i]);
      }

      int float_ival = 0;
      bool is_float = tok.size() == 8;
      for (int i = 0; i < 8 && is_float; i++) {
        if (i == 1) {
          is_float = tok[i] == '.';
        } else {
          is_float = '0' <= tok[i] && tok[i] <= '9';
          float_ival = float_ival * 10 + tok[i] - '0';
        }
      }

      verdict = "W";
      if (is_float && 0 <= float_ival && float_ival <= 1000000) {
        char buf[10];
        sprintf(buf, "%d.%06d", float_ival / 1000000, float_ival % 1000000);
        verdict = buf;
        is_num = true;
      } else {
        int tok_val = atoi(tok.c_str());
        if (0 <= tok_val && tok_val <= 100 && to_string(tok_val) == tok) {
          verdict = tok;
          is_num = true;
        }
      }
      for (int i = 0; oktokens[i].size() != 0; i++) {
        if (tok == oktokens[i]) {
          verdict = "A";
        }
      }
    } else {
      verdict = "A";
    }
  }
  cout << verdict << endl;

  if (verdict == "A" || is_num) {
    signal_ok();
  }

  return post_process_truncate(argc - 3, argv + 3);
}

int post_process_sub_extended(int argc, char** argv) {
  /* Determine the verdict. */
  string verdict;
  int num_runs = atoi(argv[1]);
  for (int i = 0; i < num_runs && verdict == ""; i++) {
    int was_sub = atoi(argv[2 + 3 * i]);
    string exitstatus = argv[3 + 3 * i];
    int exit_code = atoi(argv[4 + 3 * i]);

    if (exitstatus == "error") {
      verdict = "E";
    } else if (exitstatus == "tle") {
      verdict = was_sub ? "T" : "E";
    } else if (exitstatus == "rte") {
      verdict = was_sub ? "R" : "E";
    } else if (exitstatus == "skipped") {
      /* verdict = "W"; */
    } else if (exit_code != 0 && !was_sub) {
      verdict = "W";
    } else if (exitstatus != "ok") {
      verdict = "E";
    }
  }

  bool is_num = false;
  if (verdict == "") {
    string tok;
    if (cin >> tok) {
      for (int i = 0; i < tok.size(); i++) {
        tok[i] = toupper(tok[i]);
      }

      int float_ival = 0;
      bool is_float = tok.size() == 8;
      for (int i = 0; i < 8 && is_float; i++) {
        if (i == 1) {
          is_float = tok[i] == '.';
        } else {
          is_float = '0' <= tok[i] && tok[i] <= '9';
          float_ival = float_ival * 10 + tok[i] - '0';
        }
      }

      verdict = "W";
      if (is_float && 0 <= float_ival && float_ival <= 1000000) {
        char buf[10];
        sprintf(buf, "%d.%06d", float_ival / 1000000, float_ival % 1000000);
        verdict = buf;
        is_num = true;
      } else {
        int tok_val = atoi(tok.c_str());
        if (0 <= tok_val && tok_val <= 100 && to_string(tok_val) == tok) {
          verdict = tok;
          is_num = true;
        }
      }
      for (int i = 0; oktokens[i].size() != 0; i++) {
        if (tok == oktokens[i]) {
          verdict = "A";
        }
      }
    } else {
      verdict = "A";
    }
  }
  cout << verdict << endl;

  if (verdict == "A" || is_num) {
    signal_ok();
  }

  return post_process_truncate_ext(argc - (1 + 3 * num_runs),
                                   argv + (1 + 3 * num_runs));
}

int post_process_val(int argc, char** argv) {
  string exitstatus_val = argv[1];
  int exitcode_val = atoi(argv[2]);

  string result;
  if (exitstatus_val != "ok") {
    result = "fail";
  } else if (exitcode_val != 0) {
    result = "invalid";
  } else {
    string tok;
    if (cin >> tok) {
      for (int i = 0; i < tok.size(); i++) {
        tok[i] = toupper(tok[i]);
      }

      result = "invalid";
      for (int i = 0; oktokens[i].size() != 0; i++) {
        if (tok == oktokens[i]) {
          result = "valid";
        }
      }
    } else {
      result = "valid";
    }
  }

  cout << result << endl;

  return post_process_truncate(argc - 2, argv + 2);
}

int main(int argc, char** argv) {
    if (argc < 2) {
      return 0;
    }
    if (!strcmp(argv[1], "sub")) {
      return post_process_sub(argc - 1, argv + 1);
    } else if (!strcmp(argv[1], "subext")) {
      return post_process_sub_extended(argc - 1, argv + 1);
    } else if (!strcmp(argv[1], "val")) {
      return post_process_val(argc - 1, argv + 1);
    } else if (!strcmp(argv[1], "trunc")) {
      return post_process_truncate(argc - 1, argv + 1);
    }
    return 127;
}
