// --------------------------------------------------------------------------
// Program to perform a line-by-line difference of floating point numbers
// allowing for a given tolerance (both absolute and relative).  There can
// be zero or more floating-point numbers per line.  Behaves somewhat similar
// to the UNIX diff command.
// --------------------------------------------------------------------------

#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <cmath>
#include <cstdlib>

using namespace std;

double epsilon = 1e-6;

bool test(string a, string b)
{
    if (a == b) return true;
    
    istringstream ain(a), bin(b);

    for(;;) {
        double da, db;
        string sa, sb;

        // check for floating point entries
        if (ain >> da) {
            if (bin >> db) {
                double d = abs(da - db);
                double re = (da != 0) ? (d / da) : d;
                if (d > epsilon && re > epsilon)
                    return false;
            }
            else return false;
            continue;
        }

        ain.clear();
        if (ain >> sa) {
            if (bin >> sb) {
                if (sa != sb)
                    return false;
            }
            else return false;
        }
        else if (bin >> sb) return false;
        else break;
    }

    return true;
}

int main(int argc, char *argv[])
{
    char *file0 = 0, *file1 = 0, *file2 = 0;
    
    for (int i = 1; i < argc; ++i) {
        if (argv[i][0] == '-') {
            if (argv[i][1] == 'e') {
                if (argv[i][2]) {
                    epsilon = pow(10, -atof(argv[i] + 2));
                } else {
                    epsilon = pow(10, -atof(argv[++i]));
                }
            } else if (argv[i][1] == 'f') {
                if (argv[i][2]) {
                    epsilon = atof(argv[i] + 2);
                } else {
                    epsilon = atof(argv[++i]);
                }
            }
            continue;
        }
        if (file0) {
            if (file1) {
                if (file2)  break;
                else        file2 = argv[i];
            }
            else file1 = argv[i];
        }
        else file0 = argv[i];
    }
    
    if (file0 == 0 || file1 == 0 || file2 == 0) {
        cout << "Insufficient arguments!" << endl;
        return 1;
    }
    
    int different = 0;
    
    // start line by line comparison
    
    ifstream in1(file1);
    ifstream in2(file2);
    
    string line1, line2;
    int line = 1;
    while (getline(in1, line1) && different < 100)
    {
        if (getline(in2, line2)) {
            if (!test(line1, line2))
            {
                cout << line << "< " << line1 << endl;
                cout << line << "> " << line2 << endl;
                ++different;
            }
        }
        else if (!line1.empty()) {
            cout << line << "< " << line1 << endl;
            ++different;
        }
        ++line;
    }
    while (getline(in2, line2) && different < 100)
    {
        if (!line2.empty()) {
            cout << line << "> " << line2 << endl;
            ++different;
        }
        ++line;
    }
    
    in1.close();
    in2.close();

    return different;
}
