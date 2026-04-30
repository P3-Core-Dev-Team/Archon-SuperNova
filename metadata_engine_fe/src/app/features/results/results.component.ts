import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AnalysisService } from '../../core/services/analysis.service';
import { ActivatedRoute } from '@angular/router';
import cytoscape from 'cytoscape';

@Component({
  selector: 'app-results',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './results.component.html',
  styleUrls: []
})
export class ResultsComponent implements OnInit {
  relationships: any[] = [];
  domains: any[] = [];
  sensitive: any[] = [];
  searchQuery: string = '';
  allTables: string[] = [];
  suggestions: string[] = [];
  selectedTableRelationships: any[] = [];
  tablesData: any[] = [];
  selectedTableColumns: any[] = [];
  selectedTableRelationshipGroups: any[] = [];
  filteredTableRelationshipGroups: any[] = [];
  relSearchQuery: string = '';
  relSuggestions: string[] = [];

  jobId: number | null = null;
  cyInstance: any = null;
  activeView: 'table' | 'graph' = 'table';

  constructor(private analysisService: AnalysisService, private route: ActivatedRoute) { }

  setView(view: 'table' | 'graph') {
    this.activeView = view;
    if (view === 'graph') {
      setTimeout(() => {
        if (this.cyInstance) {
          this.cyInstance.resize();
          this.cyInstance.fit();
        } else {
          this.renderGraph();
        }
      }, 50);
    }
  }

  ngOnInit() {
    this.route.queryParams.subscribe(params => {
      if (params['job']) {
        this.jobId = +params['job'];
      }
      this.refreshData();
    });
  }

  refreshData() {
    if (!this.jobId) return;
    this.analysisService.getRelationships(this.jobId).subscribe(res => {
      this.relationships = res._embedded ? res._embedded.relationshipList.map((dbRes: any) => ({
        tableA: dbRes.sourceTable?.tableName || dbRes.sourceTableName || 'Unknown',
        columnA: dbRes.sourceColumn,
        tableB: dbRes.targetTable?.tableName || dbRes.targetTableName || 'Unknown',
        columnB: dbRes.targetColumn,
        cardinality: dbRes.cardinality || 'n:n'
      })) : [];

      const tablesSet = new Set<string>();
      this.relationships.forEach(r => {
        tablesSet.add(r.tableA);
        tablesSet.add(r.tableB);
      });
      this.allTables = Array.from(tablesSet).sort();

      this.renderGraph();
    });

    this.analysisService.getTables(this.jobId).subscribe(res => {
      this.tablesData = res._embedded ? res._embedded.discoveredTableList : [];
      this.updateGroupedTables();
      // Attempt to restore columns if a table is already selected
      if (this.searchQuery && this.searchQuery.trim() !== '') {
        this.updateSelectedRelationships(this.searchQuery);
      }
    });

    this.analysisService.getDomains(this.jobId).subscribe(res => this.domains = res._embedded ? res._embedded.domainGroupList : []);
    this.analysisService.getSensitiveColumns(this.jobId).subscribe(res => this.sensitive = res._embedded ? res._embedded.sensitiveColumnList : []);
  }

  onSearchInput() {
    if (this.searchQuery && this.searchQuery.trim() !== '') {
      const q = this.searchQuery.toLowerCase().trim();
      this.suggestions = this.allTables.filter(t => t.toLowerCase().includes(q) && t.toLowerCase() !== q);

      // If the user typed an exact match natively without clicking the suggestion
      if (this.allTables.some(t => t.toLowerCase() === q)) {
        this.updateSelectedRelationships(this.searchQuery.trim());
      } else {
        this.selectedTableRelationships = [];
      }
    } else {
      this.suggestions = [];
      this.selectedTableRelationships = [];
      this.selectedGroup = null;
    }
    this.renderGraph();
  }

  selectedGroup: string | null = null;

  selectGroup(groupName: string) {
    this.selectedGroup = groupName;
  }

  clearGroupSelection() {
    this.selectedGroup = null;
  }

  selectSuggestion(tableName: string) {
    this.searchQuery = tableName;
    this.suggestions = [];
    this.updateSelectedRelationships(tableName);
    this.relCurrentPage = 0;
    this.renderGraph();
  }

  updateSelectedRelationships(tableName: string) {
    const q = tableName.toLowerCase();
    this.selectedTableRelationships = this.relationships.filter(r =>
      r.tableA.toLowerCase() === q || r.tableB.toLowerCase() === q
    );

    // Find table columns
    const matchedTable = this.tablesData.find(t => t.tableName.toLowerCase() === q);
    this.selectedTableColumns = matchedTable && matchedTable.columns ? matchedTable.columns : [];

    // Group relationships
    const referencesMap = new Map<string, any[]>();
    const referencedByMap = new Map<string, any[]>();

    this.selectedTableRelationships.forEach(rel => {
      if (rel.tableA.toLowerCase() === q) {
        // This table references another table
        if (!referencesMap.has(rel.tableB)) referencesMap.set(rel.tableB, []);
        referencesMap.get(rel.tableB)?.push(rel);
      } else {
        // This table is referenced by another table
        if (!referencedByMap.has(rel.tableA)) referencedByMap.set(rel.tableA, []);
        referencedByMap.get(rel.tableA)?.push(rel);
      }
    });

    this.selectedTableRelationshipGroups = [];
    referencesMap.forEach((rels, targetTable) => {
      this.selectedTableRelationshipGroups.push({ type: 'references', target: targetTable, relations: rels });
    });
    referencedByMap.forEach((rels, sourceTable) => {
      this.selectedTableRelationshipGroups.push({ type: 'referenced by', target: sourceTable, relations: rels });
    });

    // Mark Foreign Keys
    this.selectedTableColumns = this.selectedTableColumns.map(col => {
      const isFK = this.selectedTableRelationships.some(rel => rel.tableA.toLowerCase() === q && rel.columnA === col.columnName);
      return { ...col, isForeignKey: isFK };
    });

    this.selectedTableType = matchedTable && matchedTable.tableType ? matchedTable.tableType : 'Standard Entity';

    this.relSearchQuery = '';
    this.colCurrentPage = 0;
    this.filterRelationships();
  }

  selectedTableType: string = 'Standard Entity';

  getHeaderColor(): string {
    const t = this.selectedTableType || '';
    return this.getTableColor(t);
  }

  getTableColor(t: string): string {
    if (!t) return 'rgba(255, 255, 255, 0.04)';
    if (t.includes('Header')) return 'rgba(209, 132, 4, 0.15)'; // Warning/Orange
    if (t.includes('Item') || t.includes('Detail')) return 'rgba(246, 166, 35, 0.1)'; // Yellow/Orange
    if (t.includes('Master')) return 'rgba(56, 189, 248, 0.15)'; // Blue
    if (t.includes('History') || t.includes('Log')) return 'rgba(107, 127, 168, 0.15)'; // Muted Slate
    if (t.includes('Config')) return 'rgba(22, 158, 113, 0.15)'; // Green
    return 'rgba(255, 255, 255, 0.04)'; // Default
  }

  getRelCount(tableName: string): number {
    const q = tableName.toLowerCase();
    return this.relationships.filter(r => r.tableA.toLowerCase() === q || r.tableB.toLowerCase() === q).length;
  }

  groupedTables: { type: string; tables: any[] }[] = [];

  updateGroupedTables() {
    const groups = new Map<string, any[]>();
    this.tablesData.forEach(t => {
      const type = t.tableType || 'Standard Entity';
      if (!groups.has(type)) {
        groups.set(type, []);
      }
      groups.get(type)?.push(t);
    });
    this.groupedTables = Array.from(groups.entries()).map(([type, tables]) => ({ type, tables }));
  }

  get selectedGroupTables() {
    if (!this.selectedGroup) return [];
    return this.groupedTables.find(g => g.type === this.selectedGroup)?.tables || [];
  }


  getHexColor(t: string): string {
    if (!t) return '#3b82f6';
    if (t.includes('Header')) return '#f59e0b'; // Orange
    if (t.includes('Item') || t.includes('Detail')) return '#fbbf24'; // Yellow
    if (t.includes('Master')) return '#38bdf8'; // Blue
    if (t.includes('History') || t.includes('Log')) return '#94a3b8'; // Slate
    if (t.includes('Config')) return '#10b981'; // Green
    return '#3b82f6'; // Default Blue
  }

  filterRelationships() {
    if (!this.relSearchQuery || this.relSearchQuery.trim() === '') {
      this.filteredTableRelationshipGroups = [...this.selectedTableRelationshipGroups];
      this.relSuggestions = [];
      this.relCurrentPage = 0;
      return;
    }
    const sq = this.relSearchQuery.toLowerCase().trim();

    // Autocomplete suggestions for relationship target tables
    this.relSuggestions = this.selectedTableRelationshipGroups
      .map(g => g.target)
      .filter(t => t.toLowerCase().includes(sq) && t.toLowerCase() !== sq);

    this.filteredTableRelationshipGroups = this.selectedTableRelationshipGroups.map(group => {
      if (group.target.toLowerCase().includes(sq)) {
        return group;
      }
      const filteredRels = group.relations.filter((r: any) =>
        r.columnA.toLowerCase().includes(sq) || r.columnB.toLowerCase().includes(sq)
      );
      if (filteredRels.length > 0) {
        return { ...group, relations: filteredRels };
      }
      return null;
    }).filter(g => g !== null);

    this.relCurrentPage = 0;
  }

  selectRelSuggestion(target: string) {
    this.relSearchQuery = target;
    this.relSuggestions = [];
    this.filterRelationships();
  }

  relCurrentPage: number = 0;
  relPageSize: number = 4;

  colCurrentPage: number = 0;
  colPageSize: number = 10;

  get paginatedColumns() {
    const start = this.colCurrentPage * this.colPageSize;
    return this.selectedTableColumns.slice(start, start + this.colPageSize);
  }

  get colTotalPages() {
    return Math.max(1, Math.ceil(this.selectedTableColumns.length / this.colPageSize));
  }

  colNextPage() {
    if (this.colCurrentPage < this.colTotalPages - 1) this.colCurrentPage++;
  }

  colPrevPage() {
    if (this.colCurrentPage > 0) this.colCurrentPage--;
  }

  get paginatedRelationshipGroups() {
    const start = this.relCurrentPage * this.relPageSize;
    return this.filteredTableRelationshipGroups.slice(start, start + this.relPageSize);
  }

  get relTotalPages() {
    return Math.ceil(this.filteredTableRelationshipGroups.length / this.relPageSize);
  }

  relNextPage() {
    if (this.relCurrentPage < this.relTotalPages - 1) {
      this.relCurrentPage++;
    }
  }

  relPrevPage() {
    if (this.relCurrentPage > 0) {
      this.relCurrentPage--;
    }
  }

  renderGraph() {
    setTimeout(() => {
      const container = document.getElementById('cy');
      if (!container) return;

      if (this.cyInstance) {
        this.cyInstance.destroy();
      }

      const elements: any[] = [];
      const nodeSet = new Set<string>();

      let relsToRender = this.relationships;
      if (this.searchQuery && this.searchQuery.trim() !== '') {
        const exactQ = this.searchQuery.toLowerCase().trim();
        relsToRender = this.relationships.filter(r =>
          r.tableA.toLowerCase() === exactQ || r.tableB.toLowerCase() === exactQ
        );
      }

      relsToRender.forEach(rel => {
        if (!nodeSet.has(rel.tableA)) {
          const tableObj = this.tablesData.find(t => t.tableName === rel.tableA);
          const tColor = this.getHexColor(tableObj?.tableType || '');
          elements.push({ data: { id: rel.tableA, label: rel.tableA, color: tColor } });
          nodeSet.add(rel.tableA);
        }
        if (!nodeSet.has(rel.tableB)) {
          const tableObj = this.tablesData.find(t => t.tableName === rel.tableB);
          const tColor = this.getHexColor(tableObj?.tableType || '');
          elements.push({ data: { id: rel.tableB, label: rel.tableB, color: tColor } });
          nodeSet.add(rel.tableB);
        }
        elements.push({
          data: {
            id: `${rel.tableA}_${rel.tableB}`,
            source: rel.tableA,
            target: rel.tableB,
            label: '=',
            fullLabel: `${rel.columnA} = ${rel.columnB}`
          }
        });
      });

      this.cyInstance = cytoscape({
        container: container,
        elements: elements,
        style: [
          {
            selector: 'node',
            style: {
              'background-color': 'data(color)',
              'shape': 'round-rectangle',
              'width': 'label',
              'height': 'label',
              'padding': '12px',
              'border-width': 1,
              'border-color': '#475569',
              'label': 'data(label)',
              'color': '#ffffff',
              'font-size': '11px',
              'font-weight': '600',
              'text-valign': 'center',
              'text-halign': 'center',
              'text-wrap': 'ellipsis',
              'text-max-width': '120px'
            }
          },
          {
            selector: 'node.hover',
            style: {
              'border-color': '#cbd5e1',
              'border-width': 2,
              'text-max-width': '200px'
            }
          },
          {
            selector: 'edge',
            style: {
              'width': 1,
              'line-color': '#475569',
              'target-arrow-color': '#475569',
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
              'label': 'data(label)',
              'text-rotation': 'autorotate',
              'font-size': '2px',
              'color': '#cbd5e1',
              'text-background-color': '#0B0F19',
              'text-background-opacity': 0.8
            }
          },
          {
            selector: 'edge.hover',
            style: {
              'label': 'data(fullLabel)',
              'font-size': '4px',
              'color': '#60a5fa',
              'text-background-color': '#1e293b'
            }
          }
        ],
        layout: {
          name: 'cose',
          padding: 30,
          nodeRepulsion: function (node: any) { return 8192; },
          idealEdgeLength: function (edge: any) { return 120; },
          edgeElasticity: function (edge: any) { return 32; },
          nestingFactor: 1.2,
          gravity: 0.25,
          numIter: 1000,
          initialTemp: 200,
          coolingFactor: 0.95,
          minTemp: 1.0,
          animate: false
        }
      });

      this.cyInstance.on('mouseover', 'node', (e: any) => {
        e.target.addClass('hover');
      });

      this.cyInstance.on('mouseout', 'node', (e: any) => {
        e.target.removeClass('hover');
      });

      this.cyInstance.on('mouseover', 'edge', (e: any) => {
        e.target.addClass('hover');
      });

      this.cyInstance.on('mouseout', 'edge', (e: any) => {
        e.target.removeClass('hover');
      });

      // Interactive Node Clicking!
      this.cyInstance.on('tap', 'node', (e: any) => {
        const nodeId = e.target.id();
        this.selectSuggestion(nodeId);
      });
    }, 100);
  }
}
