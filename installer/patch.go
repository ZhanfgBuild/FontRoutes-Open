// FontRoutes offline KSU XML stager. No files outside its output directory are written.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

const xmlLimit = 2 * 1024 * 1024

type layout struct {
	close    int
	families int
	names    map[string]bool
}

func scan(raw []byte) (layout, error) {
	l := layout{close: -1, names: map[string]bool{}}
	if !utf8.Valid(raw) || bytes.HasPrefix(raw, []byte{0xef, 0xbb, 0xbf}) {
		return l, errors.New("expected UTF-8 without BOM")
	}
	d := xml.NewDecoder(bytes.NewReader(raw))
	depth := 0
	roots := 0
	for {
		pos := d.InputOffset()
		t, err := d.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return l, err
		}
		switch x := t.(type) {
		case xml.Directive:
			return l, errors.New("XML directives, DTD and entities are unsupported")
		case xml.ProcInst:
			if x.Target != "xml" || pos != 0 {
				return l, errors.New("unsupported XML processing instruction")
			}
		case xml.CharData:
			if depth == 0 && strings.TrimSpace(string(x)) != "" {
				return l, errors.New("unexpected text outside root")
			}
		case xml.StartElement:
			if depth == 0 {
				roots++
				if roots != 1 || x.Name.Space != "" || x.Name.Local != "familyset" {
					return l, errors.New("expected one plain familyset root")
				}
			}
			if depth == 1 && x.Name.Space == "" && (x.Name.Local == "family" || x.Name.Local == "family-list") {
				l.families++
			}
			seen := map[xml.Name]bool{}
			for _, a := range x.Attr {
				if seen[a.Name] || a.Name.Local == "xmlns" || a.Name.Space == "xmlns" {
					return l, errors.New("unsupported duplicate or namespace attribute")
				}
				seen[a.Name] = true
				if (depth == 1 && a.Name.Local == "name") || a.Name.Local == "to" || a.Name.Local == "fallbackFor" {
					l.names[a.Value] = true
				}
			}
			depth++
		case xml.EndElement:
			depth--
			if depth == 0 {
				l.close = int(pos)
			}
		}
	}
	if roots != 1 || depth != 0 || l.families == 0 || l.close < 0 || !bytes.HasPrefix(raw[l.close:], []byte("</familyset")) {
		return l, errors.New("unsupported empty or unclosed familyset")
	}
	return l, nil
}

func readLimited(path string, n int) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !st.Mode().IsRegular() || st.Size() < 1 || st.Size() > int64(n) {
		return nil, errors.New("input not regular or outside size limit")
	}
	b, err := io.ReadAll(io.LimitReader(f, int64(n+1)))
	if err != nil {
		return nil, err
	}
	if len(b) < 1 || len(b) > n {
		return nil, errors.New("input exceeds limit")
	}
	return b, nil
}

func patch(stock, addition []byte) ([]byte, error) {
	in, err := scan(stock)
	if err != nil {
		return nil, err
	}
	if bytes.Contains(stock, []byte("FontRoutesOpen-")) || bytes.Contains(stock, []byte("FontRoutes Open:")) {
		return nil, errors.New("FontRoutes font XML already mounted; disable old modules and reboot")
	}
	extra, err := scan(append(append([]byte("<familyset>"), addition...), []byte("</familyset>")...))
	if err != nil {
		return nil, err
	}
	for k := range extra.names {
		if in.names[k] {
			return nil, fmt.Errorf("ROM family or alias collision: %q", k)
		}
	}
	patched := make([]byte, 0, len(stock)+len(addition))
	patched = append(patched, stock[:in.close]...)
	patched = append(patched, addition...)
	patched = append(patched, stock[in.close:]...)
	check, err := scan(patched)
	if err != nil {
		return nil, err
	}
	if check.families != in.families+extra.families || len(check.names) != len(in.names)+len(extra.names) {
		return nil, errors.New("font XML insertion post-check failed")
	}
	return patched, nil
}

func put(path string, contents []byte) error {
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		return errors.New("refusing to overwrite staging file")
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".fr-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(contents); err != nil {
		f.Close()
		return err
	}
	if err = f.Chmod(0644); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}

func run(args []string) error {
	if len(args) != 3 {
		return errors.New("expected input, fragment and staging output paths")
	}
	stock, err := readLimited(args[0], xmlLimit)
	if err != nil {
		return err
	}
	extra, err := readLimited(args[1], 32*1024)
	if err != nil {
		return err
	}
	patched, err := patch(stock, extra)
	if err != nil {
		return err
	}
	if err = put(args[2], patched); err != nil {
		return err
	}
	fmt.Printf("%x %x\n", sha256.Sum256(stock), sha256.Sum256(patched))
	return nil
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "FontRoutes XML stager:", err)
		os.Exit(1)
	}
}
