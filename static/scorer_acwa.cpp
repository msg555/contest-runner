#include <iostream>
#include <cstdlib>
#include <cstdio>

using namespace std;

int main(int argc, char** argv) {
  if (argc < 1) {
    cout << "E" << endl;
    return 0;
  }

  int tests = atoi(argv[1]);

  bool ss = false;
  string result = "A";
  for (int i = 1; i <= tests; i++) {
    char buf[16];
    sprintf(buf, "%d", i);
    FILE* fin = fopen(buf, "r");
    if (!fin) {
      if (!ss) {
        cerr << "missing judgement for " << i << endl;
      }
      ss = true;
      continue;
    }

    char verdict = fgetc(fin);
    fclose(fin);

    switch (verdict) {
      case 'A':
      case 'W':
      case 'T':
      case 'R':
        result = result == "A" ? string(1, verdict) : result;
        break;
      default:
        result = "E";
        break;
    }
  }
  if (ss && result == "A") {
    result = "E";
  }

  cout << result << endl;

  return 0;
}
