#include <iostream>
#include <cstdlib>
#include <cstdio>
#include <map>
#include <vector>
#include <sstream>
#include <set>

int tests;

using namespace std;

vector<int> parse_test_list(string s) {
  set<int> res;
  for (int i = 0; i < s.size(); i++) {
    if (s[i] == ',') {
      s[i] = ' ';
    }
  }
  istringstream sin(s);
  for (string part; sin >> part; ) {
    int dash = part.find('-');
    if (dash == string::npos) {
      int num = atoi(part.c_str());
      if (num <= 0 || tests < num || !res.insert(num).second) {
        return vector<int>();
      }
    } else {
      int lo = atoi(part.substr(0, dash).c_str());
      int hi = atoi(part.substr(dash + 1).c_str());
      if (lo <= 0 || hi < lo || tests < hi) {
        return vector<int>();
      }
      for (int i = lo; i <= hi; i++) {
        if (!res.insert(i).second) {
          return vector<int>();
        }
      }
    }
  }
  return vector<int>(res.begin(), res.end());
}

int main(int argc, char** argv) {
  if (argc < 1) {
    cout << "E" << endl;
    return 0;
  }
  tests = atoi(argv[1]);

  int lncnt = 1;
  map<int, int> spec;
  for(string ln; getline(cin, ln); lncnt++) {
    if (ln[0] == '#' || ln.empty()) {
      continue;
    }
    int sppos = ln.find(' ');
    if (sppos == string::npos) {
      cerr << "unexpected format on line " << lncnt << endl;
      cout << "E" << endl;
      return 0;
    }
    vector<int> tests = parse_test_list(ln.substr(sppos + 1));
    if (tests.empty()) {
      cerr << "invalid test list on line " << lncnt << endl;
      cout << "E" << endl;
      return 0;
    }

    int wht = atoi(ln.substr(0, sppos).c_str());
    for (int i = 0; i < tests.size(); i++) {
      spec[tests[i]] = wht;
    }
  }

  int total = 0;
  int score = 0;

  for (int i = 1; i <= tests; i++) {
    char buf[16];
    sprintf(buf, "%d", i);
    FILE* fin = fopen(buf, "r");
    if (!fin) {
      cerr << "missing judgement for test " << i << endl;
      cout << "E" << endl;
      return 0;
    }

    fgets(buf, sizeof(buf), fin);
    fclose(fin);

    if (buf[0] == 'E') {
      cout << "E" << endl;
      return 0;
    }

    int wht = spec[i];
    total += wht;
    if (buf[0] == 'A') {
      score += wht * 100;
    } else if ('0' <= buf[0] && buf[0] <= '9') {
      score += wht * atoi(buf);
    }
  }

  if (total == 0) {
    cerr << "no weights assigned" << endl;
    cout << "E" << endl;
  } else {
    cout << score / total << endl;
  }

  return 0;
}
