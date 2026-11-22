#include <iostream>
#include <vector>
#include <cstring>

using namespace std;

#define MAXN 1010

int N, edges;
vector<int> E[MAXN];
pair<int, int> M[MAXN];
int vis[MAXN];


int dfs(int u) {
  vis[u] = 1;
  for (int v : E[u]) {
    if (vis[v] == 2) {
      /* Dead end */
      continue;
    }

    if (vis[v] == 1) {
      /* Found odd cycle, make a loop */
      M[u].first = v;
      M[v].second = u;
      return v;
    }

    if (M[v].first == -1) {
      // Found unmatched
      M[u] = make_pair(v, -1);
      M[v] = make_pair(u, -1);
      return N;
    }

    if (M[v].second != -1) {
      // Found blossom, augment the blossom
      for (int i = M[v].first; i != v; ) {
        int j = M[i].first;
        int k = M[j].first;
        M[i] = make_pair(j, -1);
        M[j] = make_pair(i, -1);
        i = k;
      }
      M[u] = make_pair(v, -1);
      M[v] = make_pair(u, -1);
      return N;
    }

    /* recurse! */
    vis[v] = 2;
    int next_u = M[v].first;
    int res = dfs(next_u);

    /* Simple augmenting path */
    if (res == N) {
      M[u] = make_pair(v, -1);
      M[v] = make_pair(u, -1);
      return N;
    }

    /* Blossom augmenting path */
    if (res >= 0) {
      M[u].first = v;
      M[v].second = u;
      M[v].first = next_u;
      M[next_u].second = v;
      return u == res ? N : res;
    }
  }
  return -1;
}


int main() {
  ios_base::sync_with_stdio(false);

  cin >> N >> edges;
  for (int i = 0; i < edges; i++) {
    int u, v;
    cin >> u >> v;
    u--; v--;
    E[u].push_back(v);
    E[v].push_back(u);
  }

  /* Find augmenting path

  Start at unmatched vertex u and dfs.
  - for each adjacent v
      if v is unmatched found augmenting path
      if v is in 2-match recurse on its match as u
      if v is in odd-cycle-match found augmenting path
      if found odd length cycle found augmenting path

x - y + z - a
          \ + 
            b
  */

  bool solved = false;
  memset(M, -1, sizeof(M));
  while (true) {
    bool changes = false;
    solved = true;

/*
    cout << endl;
    for (int i = 0; i < N; i++) {
      cout << i << ": " << M[i].first << ", " << M[i].second << endl;
    }
*/

    memset(vis, 0, sizeof(vis));
    for (int u = 0; u < N; u++) {
      if (M[u].first == -1) {
        solved = false;
        if (dfs(u) != -1) {
          changes = true;
          break;
        }
      }
    }
    if (!changes) break;
  }

  if (solved) {
    for (int u = 0; u < N; u++) {
      int amt = M[u].second == -1 ? 10000 : 5000;
      for (int v = 0; v < N; v++) {
        if (v) cout << ' ';
        if (v == M[u].first || v == M[u].second) {
          cout << amt;
        } else {
          cout << '0';
        }
      }
      cout << '\n';
    }
  } else {
    cout << "-1\n";
  }

  return 0;
}
