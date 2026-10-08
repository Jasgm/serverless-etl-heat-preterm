"""Builds reference/tx_county_regions.csv from the DSHS Center for Health Statistics table
"Texas County Numbers and Public Health Regions"
https://www.dshs.texas.gov/center-health-statistics/texas-county-numbers-public-health-regions

Counties are listed in DSHS county-number order (1..254); FIPS = 48001 + 2*(n-1).
HSR (the 8 administrative regions) follows the DSHS rule: 2+3 -> 2/3, 4 -> 4/5N,
5 -> 4/5N except Hardin/Jefferson/Orange -> 6/5S, 6 -> 6/5S, 9+10 -> 9/10.
"""
import csv, os

COUNTIES = """Anderson 4|Andrews 9|Angelina 5|Aransas 11|Archer 2|Armstrong 1|Atascosa 8|Austin 6|Bailey 1|Bandera 8|
Bastrop 7|Baylor 2|Bee 11|Bell 7|Bexar 8|Blanco 7|Borden 1|Bosque 7|Bowie 4|Brazoria 6|
Brazos 7|Brewster 10|Briscoe 1|Brooks 11|Brown 2|Burleson 7|Burnet 7|Caldwell 7|Calhoun 8|Callahan 2|
Cameron 11|Camp 4|Carson 1|Cass 4|Castro 1|Chambers 6|Cherokee 4|Childress 1|Clay 2|Cochran 1|
Coke 9|Coleman 2|Collin 3|Collingsworth 1|Colorado 6|Comal 8|Comanche 2|Concho 9|Cooke 3|Coryell 7|
Cottle 2|Crane 9|Crockett 9|Crosby 1|Culberson 10|Dallam 1|Dallas 3|Dawson 1|Deaf Smith 1|Delta 4|
Denton 3|DeWitt 8|Dickens 1|Dimmit 8|Donley 1|Duval 11|Eastland 2|Ector 9|Edwards 8|Ellis 3|
El Paso 10|Erath 3|Falls 7|Fannin 3|Fayette 7|Fisher 2|Floyd 1|Foard 2|Fort Bend 6|Franklin 4|
Freestone 7|Frio 8|Gaines 1|Galveston 6|Garza 1|Gillespie 8|Glasscock 9|Goliad 8|Gonzales 8|Gray 1|
Grayson 3|Gregg 4|Grimes 7|Guadalupe 8|Hale 1|Hall 1|Hamilton 7|Hansford 1|Hardeman 2|Hardin 5|
Harris 6|Harrison 4|Hartley 1|Haskell 2|Hays 7|Hemphill 1|Henderson 4|Hidalgo 11|Hill 7|Hockley 1|
Hood 3|Hopkins 4|Houston 5|Howard 9|Hudspeth 10|Hunt 3|Hutchinson 1|Irion 9|Jack 2|Jackson 8|
Jasper 5|Jeff Davis 10|Jefferson 5|Jim Hogg 11|Jim Wells 11|Johnson 3|Jones 2|Karnes 8|Kaufman 3|Kendall 8|
Kenedy 11|Kent 2|Kerr 8|Kimble 9|King 1|Kinney 8|Kleberg 11|Knox 2|Lamar 4|Lamb 1|
Lampasas 7|La Salle 8|Lavaca 8|Lee 7|Leon 7|Liberty 6|Limestone 7|Lipscomb 1|Live Oak 11|Llano 7|
Loving 9|Lubbock 1|Lynn 1|McCulloch 9|McLennan 7|McMullen 11|Madison 7|Marion 4|Martin 9|Mason 9|
Matagorda 6|Maverick 8|Medina 8|Menard 9|Midland 9|Milam 7|Mills 7|Mitchell 2|Montague 2|Montgomery 6|
Moore 1|Morris 4|Motley 1|Nacogdoches 5|Navarro 3|Newton 5|Nolan 2|Nueces 11|Ochiltree 1|Oldham 1|
Orange 5|Palo Pinto 3|Panola 4|Parker 3|Parmer 1|Pecos 9|Polk 5|Potter 1|Presidio 10|Rains 4|
Randall 1|Reagan 9|Real 8|Red River 4|Reeves 9|Refugio 11|Roberts 1|Robertson 7|Rockwall 3|Runnels 2|
Rusk 4|Sabine 5|San Augustine 5|San Jacinto 5|San Patricio 11|San Saba 7|Schleicher 9|Scurry 2|Shackelford 2|Shelby 5|
Sherman 1|Smith 4|Somervell 3|Starr 11|Stephens 2|Sterling 9|Stonewall 2|Sutton 9|Swisher 1|Tarrant 3|
Taylor 2|Terrell 9|Terry 1|Throckmorton 2|Titus 4|Tom Green 9|Travis 7|Trinity 5|Tyler 5|Upshur 4|
Upton 9|Uvalde 8|Val Verde 8|Van Zandt 4|Victoria 8|Walker 6|Waller 6|Ward 9|Washington 7|Webb 11|Wharton 6|
Wheeler 1|Wichita 2|Wilbarger 2|Willacy 11|Williamson 7|Wilson 8|Winkler 9|Wise 3|Wood 4|Yoakum 1|
Young 2|Zapata 11|Zavala 8"""

FIVE_SOUTH = {"Hardin", "Jefferson", "Orange"}
HSR = {1: "1", 2: "2/3", 3: "2/3", 4: "4/5N", 6: "6/5S", 7: "7", 8: "8", 9: "9/10", 10: "9/10", 11: "11"}


def rows():
    entries = [e.strip() for e in COUNTIES.replace("\n", "").split("|") if e.strip()]
    for n, entry in enumerate(entries, start=1):
        name, phr = entry.rsplit(" ", 1)
        phr = int(phr)
        hsr = ("6/5S" if name in FIVE_SOUTH else "4/5N") if phr == 5 else HSR[phr]
        yield {"county_fips": f"{48001 + 2 * (n - 1):05d}", "county_name": name,
               "dshs_county_number": n, "phr": phr, "hsr": hsr}


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "src", "common", "tx_county_regions.csv")
    data = list(rows())
    assert len(data) == 254, len(data)
    assert data[-1]["county_fips"] == "48507" and data[-1]["county_name"] == "Zavala"
    assert len({d["hsr"] for d in data}) == 8
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0]), lineterminator="\n")
        w.writeheader(); w.writerows(data)
    print(f"wrote {len(data)} counties -> {os.path.normpath(out)}")
