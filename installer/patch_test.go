package main

import (
	"bytes"
	"testing"
)

func TestRoundTrip(t *testing.T) {
	stock := []byte("<?xml version=\"1.0\"?>\r\n<familyset version=\"23\">\r\n<!-- </familyset> -->\r\n<family><font>r.ttf</font></family>\r\n<alias name=\"oem\" to=\"other\"/>\r\n</familyset>\r\n")
	frag := []byte("\n<family name=\"fontroutes-open-sans\"><font>font.ttf</font></family>\n")
	out, err := patch(stock, frag)
	if err != nil || !bytes.Equal(bytes.Replace(out, frag, nil, 1), stock) {
		t.Fatalf("round trip: %q, %v", out, err)
	}
}
func TestReject(t *testing.T) {
	frag := []byte("<family name=\"fontroutes-open-sans\"><font>font.ttf</font></family>")
	for _, stock := range []string{
		"<familyset/>",
		"<!DOCTYPE familyset [<!ENTITY a SYSTEM '/system/build.prop'>]><familyset><family>&a;</family></familyset>",
		"<familyset><family a='a' a='b'/></familyset>",
		"<familyset xmlns='urn:x'><family/></familyset>",
		"<familyset><family/><alias name='oem' to='fontroutes-open-sans'/></familyset>",
		"<familyset><family><font>FontRoutesOpen-existing.ttf</font></family></familyset>",
		"<familyset><family></familyset>",
		"<familyset><family/></familyset><familyset/>",
	} {
		if _, err := patch([]byte(stock), frag); err == nil {
			t.Errorf("accepted unsupported stock %q", stock)
		}
	}
}
