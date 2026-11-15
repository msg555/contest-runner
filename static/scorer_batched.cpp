#include <iostream>
#include <cstdlib>
#include <cstdio>
#include <map>
#include <vector>
#include <sstream>
#include <set>
#include <cstring>

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
  vector<pair<int, vector<int> > > batches;
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
    batches.push_back(make_pair(wht, tests));
  }

  vector<int> scores(1, 0);
  vector<string> verdicts(1, "");
  for (int i = 1; i <= tests; i++) {
    char buf[16];
    sprintf(buf, "%d", i);
    FILE* fin = fopen(buf, "r");
    if (!fin) {
      scores.push_back(0);
      verdicts.push_back("M");
      continue;
    }

    fgets(buf, sizeof(buf), fin);
    int buflen = strlen(buf);
    if (buflen > 0 && buf[buflen - 1] == '\n') {
      buf[buflen - 1] = 0;
    }
    fclose(fin);

    if (buf[0] == 'E') {
      cout << "E" << endl;
      return 0;
    }
    if (buf[0] == 'A') {
      scores.push_back(100);
    } else if ('0' <= buf[0] && buf[0] <= '9') {
      scores.push_back(atoi(buf));
    } else {
      scores.push_back(0);
    }
    verdicts.push_back(buf);
  }

  int score = 0;
  int total = 0;
  string batch_verdicts = "[";
  for (int i = 0; i < batches.size(); i++) {
    int val = 100;
    string verdict = "A";
    for (int j = 0; j < batches[i].second.size(); j++) {
      string verd = verdicts[batches[i].second[j]];
      int score = scores[batches[i].second[j]];
      if (score == 0 && verdict == "A" && verd != "0") {
        verdict = verd;
      }
      val = min(val, score);
    }
    if (verdict == "A" && val < 100) {
      verdict = to_string(val);
    }
    score += val * batches[i].first;
    total += batches[i].first;

    if (i) {
      batch_verdicts += ",";
    }
    batch_verdicts += string("\"") + verdict + "\"";

    if (verdict == "M") {
      cerr << "Batch decided by missing case" << endl;
      cout << "E" << endl;
      return 0;
    }
  }
  batch_verdicts += "]";

  if (total == 0) {
    cerr << "no batches assigned" << endl;
    cout << "E" << endl;
  } else {
    cout << score / total << endl;
    cout << batch_verdicts << endl;
  }

  return 0;
}
