#include <iostream>

using namespace std;

/* Ensures that the input:
 *
 * 1) contains only ASCII characters
 * 2) Only uses spaces and line feeds as whitespace
 * 3) Contains no other non-printable characters
 */
int main() {
  bool end_on_line = true;
  for (int ch = cin.get(); ch != -1; ch = cin.get()) {
    if (ch == '\n') {
      end_on_line = true;
    } else if (ch < 32 || ch == 127) {
      cout << "invalid non printable character\n";
      return 1;
    } else if (128 <= ch) {
      cout << "non ASCII character\n";
      return 1;
    }
  }
  if (!end_on_line) {
    cout << "test does not end on new line\n";
    return 1;
  }
  return 0;
}
